"""Backend contract and deterministic, sector-addressable virtual drive."""
from dataclasses import dataclass
from typing import Protocol
import hashlib
import math
import struct

RAW = 2352
MODES = {"MODE1": (16, 2048), "MODE2_FORM1": (24, 2048),
         "MODE2_FORM2": (24, 2324), "AUDIO": (0, RAW)}

class DriveError(Exception):
    def __init__(self, code, lba=None):
        self.code, self.lba = code, lba
        super().__init__(f"{code} at {lba}")

@dataclass(frozen=True)
class Track:
    number: int
    start: int
    end: int  # exclusive; INDEX 01, LBA 0 = 00:02:00 absolute MSF
    mode: str
    def __post_init__(self):
        if self.number < 1 or self.start < 0 or self.end <= self.start or self.mode not in MODES:
            raise ValueError("invalid track")

@dataclass(frozen=True)
class Disc:
    identity: str
    tracks: tuple[Track, ...]
    def __post_init__(self):
        if not self.identity or not self.tracks or self.tracks[0].start != 0:
            raise ValueError("disc must start at LBA zero")
        for i, t in enumerate(self.tracks):
            if t.number != i+1 or (i and t.start != self.tracks[i-1].end):
                raise ValueError("tracks must be consecutive and contiguous")
    @property
    def leadout(self):
        return self.tracks[-1].end
    def track(self, lba):
        for t in self.tracks:
            if t.start <= lba < t.end:
                return t
        raise DriveError("out_of_range", lba)

@dataclass(frozen=True)
class MediaState:
    connected: bool
    tray_open: bool
    disc_id: str | None
    generation: int  # invalidate cached sectors whenever this changes

class OpticalBackend(Protocol):
    """All operations synchronous; caller serializes access. No partial successful reads.

    Real adapters must enforce their own finite I/O deadline and translate OS/SCSI
    failures to DriveError. Raw reads return exactly count * 2352 bytes.
    """
    def state(self) -> MediaState: ...
    def toc(self) -> Disc: ...
    def read_raw(self, lba: int, count: int = 1) -> bytes: ...
    def eject(self) -> None: ...
    def insert(self, disc: Disc) -> None: ...


def bcd(n):
    return (n // 10 << 4) | n % 10


def sector(disc, lba):
    t = disc.track(lba)
    if t.mode == "AUDIO":
        # 588 signed little-endian stereo samples, 44.1 kHz, 440 Hz.
        return b"".join(struct.pack("<hh", v, v) for v in
                        (int(12000 * math.sin(2*math.pi*440*(lba*588+i)/44100)) for i in range(588)))
    frame = lba + 150
    header = bytes([bcd(frame//4500), bcd(frame//75%60), bcd(frame%75),
                    1 if t.mode == "MODE1" else 2])
    offset, size = MODES[t.mode]
    raw = bytearray(RAW)
    raw[:16] = b"\x00" + b"\xff"*10 + b"\x00" + header
    if offset == 24:
        sub = bytes([1, 0, 0x28 if t.mode == "MODE2_FORM2" else 0x08, 0])
        raw[16:24] = sub * 2
    seed = hashlib.sha256(f"{disc.identity}:{lba}".encode()).digest()
    raw[offset:offset+size] = (seed * ((size+31)//32))[:size]
    # Deliberately no EDC/ECC: fixtures are structural PS1-like sectors, not bootable media.
    return bytes(raw)


def fixtures():
    return {
        "data": Disc("synthetic-data-1", (Track(1,0,40,"MODE2_FORM1"),)),
        "mixed": Disc("synthetic-mixed-1", (Track(1,0,24,"MODE2_FORM1"), Track(2,24,40,"AUDIO"))),
        "audio": Disc("synthetic-audio-1", (Track(1,0,20,"AUDIO"),)),
        "forms": Disc("synthetic-forms-1", (Track(1,0,10,"MODE1"), Track(2,10,20,"MODE2_FORM2"))),
        "disc2": Disc("synthetic-data-2", (Track(1,0,40,"MODE2_FORM1"),)),
    }

class VirtualDrive:
    def __init__(self, disc=None):
        self.disc, self.connected, self.tray, self.generation = disc, True, disc is None, 0
        self.failures = {}  # LBA -> remaining failures; -1 is permanent
        self.disconnect_at = None
    def state(self):
        return MediaState(self.connected, self.tray, self.disc.identity if self.disc else None, self.generation)
    def _ready(self):
        if not self.connected:
            raise DriveError("disconnected")
        if self.tray or self.disc is None:
            raise DriveError("no_medium")
    def toc(self):
        self._ready()
        return self.disc
    def read_raw(self, lba, count=1):
        self._ready()
        if type(lba) is not int or type(count) is not int or lba < 0 or count < 1 or count > 4096:
            raise DriveError("invalid_request", lba)
        if lba+count > self.disc.leadout:
            raise DriveError("out_of_range", lba)
        for pos in range(lba, lba+count):
            if pos == self.disconnect_at:
                self.disconnect()
                raise DriveError("disconnected", pos)
            left = self.failures.get(pos, 0)
            if left:
                if left > 0:
                    self.failures[pos] -= 1
                raise DriveError("read_failure", pos)
        return b"".join(sector(self.disc, p) for p in range(lba,lba+count))
    def read_user(self, lba):
        raw = self.read_raw(lba)
        mode = self.disc.track(lba).mode
        if mode == "AUDIO":
            raise DriveError("not_data", lba)
        offset, size = MODES[mode]
        return raw[offset:offset+size]
    def eject(self):
        self._ready()
        self.disc, self.tray = None, True
        self.generation += 1
    def insert(self, disc):
        if not self.connected:
            raise DriveError("disconnected")
        if not self.tray:
            raise DriveError("tray_closed")
        self.disc, self.tray = disc, False
        self.generation += 1
    def disconnect(self):
        self.connected = False
        self.generation += 1
    def reconnect(self):
        self.connected = True
        self.generation += 1


def read_retry(drive: OpticalBackend, lba, attempts=3):
    if attempts < 1:
        raise ValueError("attempts must be positive")
    generation = drive.state().generation
    for attempt in range(attempts):
        if drive.state().generation != generation:
            raise DriveError("medium_changed", lba)
        try:
            data = drive.read_raw(lba)
            if len(data) != RAW:
                raise DriveError("short_read", lba)
            if drive.state().generation != generation:
                raise DriveError("medium_changed", lba)
            return data
        except DriveError as exc:
            if exc.code != "read_failure" or attempt+1 == attempts:
                raise
