"""The tick tape (ADR-0006): every broker message, exactly as received, so any day can be replayed exactly.

Layout: <tape_dir>/<source>/<YYYY-MM-DD>/<HH>[-n].tape  (IST hour; -1, -2 ... when a file for that hour already exists,
e.g. after a restart). A file is a header line then blocks:

    header : b"TANUTAPE2\\n" + one JSON line {"source", "day", "created", "format": 2}
    block  : b"B" <u32 raw_len> <u32 zlen> <zstd bytes of raw_len bytes of records>
    record : <f64 receive_time> <u8 kind> <u32 length> <payload>       (little-endian)

kind 0 = a broker market message (raw bytes), kind 1 = a control event (JSON: connect, disconnect, subscribe ...).
Each block is compressed on its own, so a crash loses at most the last unwritten second, never the file. Writing
happens on a writer thread; producers only append to a queue (never wait for the disk). If free disk falls below the
limit the writer pauses (and logs it) instead of filling the disk.
"""
from __future__ import annotations

import datetime as dt
import json
import queue
import shutil
import struct
import threading
import time
from pathlib import Path
from typing import Iterator

import pyarrow as pa

from ..core import log

MAGIC = b"TANUTAPE2\n"
KIND_MSG, KIND_CTRL = 0, 1
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_REC = struct.Struct("<dBI")
_BLK = struct.Struct("<cII")
_log = log.get("tape")


def day_dir(tape_dir: Path | str, source: str, day: str) -> Path:
    return Path(tape_dir) / source / day


class TapeWriter:
    """Append-only, thread-safe. write() never blocks on disk; close() flushes everything."""

    def __init__(self, tape_dir: Path | str, source: str, *, flush_s: float = 1.0, min_free_gb: float = 5.0,
                 clock=time.time, max_queue: int = 2_000_000):
        self.tape_dir, self.source = Path(tape_dir), source
        self.flush_s, self.min_free_gb, self.clock = flush_s, min_free_gb, clock
        self.q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.stats = {"records": 0, "bytes_raw": 0, "bytes_disk": 0, "blocks": 0, "dropped": 0, "files": 0,
                      "paused": False}
        self._fh = None
        self._hour = None
        self._last_disk_check = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="tanu-tape")
        self._thread.start()

    # ---- producers
    def write(self, payload: bytes, kind: int = KIND_MSG, t: float | None = None) -> None:
        try:
            self.q.put_nowait((self.clock() if t is None else t, kind, payload))
        except queue.Full:
            self.stats["dropped"] += 1

    def control(self, event: str, t: float | None = None, **fields) -> None:
        self.write(json.dumps({"event": event, **fields}, separators=(",", ":")).encode(), KIND_CTRL, t)

    def close(self, timeout: float = 10.0) -> dict:
        self._stop.set()
        self._thread.join(timeout)
        return dict(self.stats)

    # ---- writer thread
    def _run(self):
        buf, first = [], None
        while True:
            try:
                item = self.q.get(timeout=0.1)
                buf.append(item)
                first = first or time.monotonic()
            except queue.Empty:
                pass
            due = first is not None and time.monotonic() - first >= self.flush_s
            if buf and (due or len(buf) >= 20000 or self._stop.is_set()):
                self._flush(buf)
                buf, first = [], None
            if self._stop.is_set() and self.q.empty() and not buf:
                break
        if self._fh:
            self._fh.close()
            self._fh = None
        _log.info("tape_closed", source=self.source, **{k: v for k, v in self.stats.items() if k != "paused"})

    def _disk_ok(self) -> bool:
        now = time.monotonic()
        if now - self._last_disk_check > 30:
            self._last_disk_check = now
            self.tape_dir.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.tape_dir).free / 1e9
            paused = free < self.min_free_gb
            if paused != self.stats["paused"]:
                (_log.warning if paused else _log.info)("tape_paused_low_disk" if paused else "tape_resumed",
                                                       free_gb=round(free, 1), limit_gb=self.min_free_gb)
            self.stats["paused"] = paused
        return not self.stats["paused"]

    def _file_for(self, t: float):
        d = dt.datetime.fromtimestamp(t, IST)
        hour = (d.date().isoformat(), d.hour)
        if self._fh is not None and hour == self._hour:
            return self._fh
        if self._fh is not None:
            self._fh.close()
        folder = day_dir(self.tape_dir, self.source, hour[0])
        folder.mkdir(parents=True, exist_ok=True)
        path, n = folder / f"{hour[1]:02d}.tape", 0
        while path.exists():
            n += 1
            path = folder / f"{hour[1]:02d}-{n}.tape"
        self._fh = open(path, "ab")
        self._fh.write(MAGIC + json.dumps({"source": self.source, "day": hour[0], "created": t, "format": 2}).encode()
                       + b"\n")
        self._hour = hour
        self.stats["files"] += 1
        _log.info("tape_file_opened", file=str(path))
        return self._fh

    def _flush(self, items: list):
        if not self._disk_ok():
            self.stats["dropped"] += len(items)
            return
        # one block per hour file (a batch can cross an hour boundary)
        groups: dict = {}
        for t, k, p in items:
            d = dt.datetime.fromtimestamp(t, IST)
            groups.setdefault((d.date().isoformat(), d.hour), []).append((t, k, p))
        for _, rows in sorted(groups.items()):
            raw = b"".join(_REC.pack(t, k, len(p)) + p for t, k, p in rows)
            z = pa.compress(raw, codec="zstd", asbytes=True)
            fh = self._file_for(rows[0][0])
            fh.write(_BLK.pack(b"B", len(raw), len(z)) + z)
            fh.flush()
            self.stats["records"] += len(rows)
            self.stats["bytes_raw"] += len(raw)
            self.stats["bytes_disk"] += _BLK.size + len(z)
            self.stats["blocks"] += 1


def read_file(path: Path | str) -> Iterator[tuple[float, int, bytes]]:
    """Records of one tape file in order. A cut-off last block (crash) ends the file quietly; it is logged."""
    with open(path, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"{path} is not a Tanu tape")
        f.readline()
        while True:
            h = f.read(_BLK.size)
            if not h:
                return
            if len(h) < _BLK.size:
                _log.warning("tape_truncated", file=str(path), where="block_header")
                return
            tag, raw_len, zlen = _BLK.unpack(h)
            z = f.read(zlen)
            if tag != b"B" or len(z) < zlen:
                _log.warning("tape_truncated", file=str(path), where="block_body")
                return
            raw = pa.decompress(z, decompressed_size=raw_len, codec="zstd", asbytes=True)
            i = 0
            while i < len(raw):
                t, k, n = _REC.unpack_from(raw, i)
                i += _REC.size
                yield t, k, raw[i:i + n]
                i += n


def header(path: Path | str) -> dict:
    with open(path, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"{path} is not a Tanu tape")
        return json.loads(f.readline())


def _order(p: Path):
    stem = p.name[:-len(".tape")]
    hour, _, n = stem.partition("-")
    return int(hour), int(n or 0)


def files(tape_dir: Path | str, source: str, day: str) -> list[Path]:
    return sorted(day_dir(tape_dir, source, day).glob("*.tape"), key=_order)


def read_day(tape_dir: Path | str, source: str, day: str) -> Iterator[tuple[float, int, bytes]]:
    for p in files(tape_dir, source, day):
        yield from read_file(p)


def summary(tape_dir: Path | str, source: str, day: str, gap_s: float = 60.0) -> dict:
    """What a day's tape holds: files, records, bytes, first / last time, and silences longer than gap_s seconds
    between market messages (feed down or market closed)."""
    fs = files(tape_dir, source, day)
    n = {KIND_MSG: 0, KIND_CTRL: 0}
    first = last = prev = None
    gaps, controls = [], []
    for t, k, p in (r for f in fs for r in read_file(f)):
        n[k] = n.get(k, 0) + 1
        if k == KIND_CTRL:
            controls.append({"t": t, **json.loads(p)})
            continue
        first = t if first is None else first
        if prev is not None and t - prev > gap_s:
            gaps.append([prev, t, round(t - prev, 1)])
        prev = last = t
    return {"day": day, "source": source, "files": len(fs), "bytes": sum(f.stat().st_size for f in fs),
            "messages": n[KIND_MSG], "controls": controls, "first": first, "last": last, "gaps": gaps}
