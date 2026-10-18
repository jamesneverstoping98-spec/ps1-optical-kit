# Adapter and SwanStationPS5 integration notes

Verified 2026-10-18 by cloning the actual repository (not relying on search snippets):
https://github.com/darkxex/SwanStationPS5
Commit: b995783c93b1e28560a23d67335e5ee2e257e2db
No upstream code is bundled or linked into this kit. No PS5 compilation or emulator execution was performed.

Verified source links, pinned to that revision:
- https://github.com/darkxex/SwanStationPS5/blob/b995783c93b1e28560a23d67335e5ee2e257e2db/src/cdscsi.h
- https://github.com/darkxex/SwanStationPS5/blob/b995783c93b1e28560a23d67335e5ee2e257e2db/src/discimport.c
- https://github.com/darkxex/SwanStationPS5/blob/b995783c93b1e28560a23d67335e5ee2e257e2db/src/discplay.h
- https://github.com/darkxex/SwanStationPS5/blob/b995783c93b1e28560a23d67335e5ee2e257e2db/src/disc.h
- https://github.com/darkxex/SwanStationPS5/blob/b995783c93b1e28560a23d67335e5ee2e257e2db/src/nfs/nfs.cpp
- https://github.com/darkxex/SwanStationPS5/blob/b995783c93b1e28560a23d67335e5ee2e257e2db/src/core/host.c

## Actual boundaries observed

`cdscsi_open(unsigned unit, CdPass *pass)` and `cdscsi_cmd(const CdPass *, const uint8_t *cdb, int cdb_len, void *buf, uint32_t len, uint32_t timeout_ms, uint32_t *sense, uint32_t *got)` are the source-level transport boundary, with close and locking functions. `cdscsi_cmd` documents 0 for completion, -2 for ioctl/disconnect failure, and -3 for SCSI error or short read. Sense packs key/ASC/ASCQ. A non-null got allows variable-length TOC responses. Its header says other platforms fail; this is not a portable plugin API.

`discimport.c` uses READ TOC (0x43) and READ CD (0xBE), 2352-byte sectors, 16-sector blocks, flags 0xF8 for data and 0x10 for audio. Its private `read_user` extracts 2048 bytes at raw offset 24, so it must not be assumed mode-agnostic. `DiscReadSectors` in disc.h is a bool callback receiving context, LBA, output, and byte length for user data used by `disc_serial_from_reader`. This kit does not exercise that serial parser.

Current source additionally exposes `/discplay` virtual CUE/BIN files: `discplay_file(const char *name, uint64_t *size)` returns kind 0/1/2 and `discplay_read(int kind, uint64_t offset, void *dst, size_t n)` returns bytes, possibly short at EOF or media loss. `src/nfs/nfs.cpp` dispatches virtual reads there. `discimport.h` exposes asynchronous discplay lifecycle; it warns not to free playback memory while a game is loaded. README's import-only explanation is incomplete for this pinned source.

`src/core/host.c` stores libretro disk callbacks and calls set_eject_state and set_image_index for game disc changes. A virtual-drive swap alone does not demonstrate those callbacks or emulator state behavior.

## Proposed real-drive adapter (not implemented)

Provide an OS transport behind OpticalBackend: Windows SCSI pass-through, Linux SG_IO, or the PS5 CAM implementation, with explicit capability discovery. Map device readiness, no medium, unit attention/media change, illegal request, read error, timeout, permission denial, and disconnect to stable errors. Read TOC with transfer-length validation and translate track control bits/LBA/leadout; reject unsupported session/pregap/subchannel layouts explicitly. Read raw CDDA and data using appropriate READ CD flags. Split transfers by track and transport limit; reject short transfers rather than silently padding. Enforce deadlines in the OS call, serialize ownership, and invalidate all buffers on media generation changes. Eject or insertion can be unsupported on real devices: report the capability and use physical insertion/polling instead. Never treat read errors as permission to return valid-looking zero data.

Next hardware validation must record OS/console build, transport, vendor/model/firmware, USB bridge, sense bytes, requested/actual transfer lengths, TOC, exact media identity, and repeated hashes. Establish safe cancellation/disconnect behavior before importing full discs. These are future steps, not results delivered here.

## Proposed SwanStationPS5 bridge (not implemented)

1. Start with exported BIN/CUE/M3U files as development fixtures, checking parser behavior independently; they cannot boot a game.
2. For host-side C tests, introduce an injected transport vtable around the existing cdscsi boundary and replay virtual TOC/sector responses. The Python protocol is an architecture contract, not ABI-compatible with CdPass. A C shim or serialized IPC bridge would be required and must define byte ownership, errors, and versioning.
3. Separately test the discplay virtual file boundary with arbitrary offset reads, EOF, concurrent background reads, cancellation, and media loss. Python currently supplies raw sectors, not that asynchronous cache.
4. Test libretro eject/index/close ordering with real core callbacks; discard old media cache before accepting the new disc. The kit currently tests only its own tray/generation state.
5. Build the pinned PS5 source with its required toolchain and obtain actual runtime results before changing swanstation_integration_tested to true. Review upstream GPL licensing before distributing a linked bridge.

There is no drop-in SwanStationPS5 adapter, USB driver, SCSI command emulator, or tested integration in this deliverable.
