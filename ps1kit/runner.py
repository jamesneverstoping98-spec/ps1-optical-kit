"""Executable software-only compatibility probes and fixture export."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from .drive import VirtualDrive, fixtures, sector, read_retry, DriveError


def export(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, disc in fixtures().items():
        (directory / (name+".bin")).write_bytes(b"".join(sector(disc, lba) for lba in range(disc.leadout)))
        lines = [f'FILE "{name}.bin" BINARY']
        for t in disc.tracks:
            mode = "AUDIO" if t.mode == "AUDIO" else ("MODE1/2352" if t.mode == "MODE1" else "MODE2/2352")
            lines += [f"  TRACK {t.number:02} {mode}", f"    INDEX 01 {t.start//4500:02}:{t.start//75%60:02}:{t.start%75:02}"]
        (directory/(name+".cue")).write_text("\n".join(lines)+"\n", encoding="utf-8")
    (directory/"multi.m3u").write_text("data.cue\ndisc2.cue\n", encoding="utf-8")


def run():
    cases=[]
    def check(name, fn):
        try:
            details=fn()
            cases.append(dict(id=name, status="pass", details=details))
        except Exception as exc:
            cases.append(dict(id=name, status="fail", details=str(exc)))
    def require(condition, message):
        if not condition:
            raise AssertionError(message)
    def layout(name):
        disc=fixtures()[name]; drive=VirtualDrive(disc)
        digest=hashlib.sha256()
        for t in disc.tracks:
            raw=drive.read_raw(t.start,t.end-t.start)
            require(len(raw)==(t.end-t.start)*2352,"wrong transfer size")
            digest.update(raw)
        require(drive.toc()==disc,"TOC mismatch")
        return dict(toc=asdict(disc), raw_sha256=digest.hexdigest())
    for name in fixtures():
        check("layout_"+name, lambda name=name: layout(name))
    def failure(permanent):
        drive=VirtualDrive(fixtures()["data"]); drive.failures[3]=-1 if permanent else 2
        try:
            raw=read_retry(drive,3)
        except DriveError as exc:
            require(permanent and exc.code=="read_failure","unexpected failure")
            return {"expected_error":exc.code,"attempts":3}
        require(not permanent and raw==sector(drive.disc,3),"retry mismatch")
        return {"recovered_after_failures":2}
    check("transient_retry",lambda: failure(False))
    check("permanent_failure",lambda: failure(True))
    def disconnect():
        drive=VirtualDrive(fixtures()["mixed"]); drive.disconnect_at=25
        try:
            drive.read_raw(24,2)
        except DriveError as exc:
            require(exc.code=="disconnected" and not drive.state().connected,"disconnect mismatch")
            drive.disconnect_at=None; drive.reconnect()
            require(len(drive.read_raw(25))==2352,"reconnect failed")
            return {"expected_error":exc.code,"reconnected":True}
        raise AssertionError("disconnect not detected")
    check("disconnect_reconnect",disconnect)
    def swap():
        drive=VirtualDrive(fixtures()["data"]); old=drive.state(); first=drive.read_raw(0)
        drive.eject()
        try:
            drive.read_raw(0)
        except DriveError as exc:
            require(exc.code=="no_medium","wrong empty-tray error")
        else:
            raise AssertionError("read with open tray")
        drive.insert(fixtures()["disc2"])
        require(drive.state().generation>old.generation and drive.read_raw(0)!=first,"stale disc")
        return {"from":old.disc_id,"to":drive.state().disc_id}
    check("multi_disc_swap",swap)
    return dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
                scope="synthetic_virtual_backend", hardware_tested=False, games_tested=False,
                swanstation_integration_tested=False, backend="ps1kit.VirtualDrive",
                summary={"passed":sum(c["status"]=="pass" for c in cases),
                         "failed":sum(c["status"]=="fail" for c in cases)}, cases=cases)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path,default=Path("results/compatibility.json"))
    parser.add_argument("--export",type=Path,help="export synthetic BIN/CUE/M3U fixtures")
    args=parser.parse_args()
    if args.export:
        export(args.export)
    report=run(); args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"]))
    return 1 if report["summary"]["failed"] else 0
