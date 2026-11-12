"""The tick tape (ADR-0006): exact round trip, hourly files, restarts, crash safety, disk guard, day summary."""
import datetime as dt
import os
import time
from collections import namedtuple

from tanu.data import tape as T

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _t(h, m, s=0.0):
    return dt.datetime(2026, 11, 13, h, m, tzinfo=IST).timestamp() + s


def test_round_trip_is_exact_and_ordered(tmp_path):
    w = T.TapeWriter(tmp_path, "upstox", flush_s=0.05)
    sent = []
    w.control("connect", t=_t(9, 14), url="wss://x")
    for i in range(5000):
        p = os.urandom(i % 300)                      # any bytes, any length (protobuf messages are binary)
        w.write(p, t=_t(9, 15, i * 0.01))
        sent.append((_t(9, 15, i * 0.01), T.KIND_MSG, p))
    st = w.close()
    got = list(T.read_day(tmp_path, "upstox", "2026-11-13"))
    assert got[0][1] == T.KIND_CTRL and b'"event":"connect"' in got[0][2]
    assert got[1:] == sent
    assert st["records"] == 5001 and st["dropped"] == 0 and st["bytes_disk"] < st["bytes_raw"] * 1.05 + 4000  # random bytes barely compress
    assert T.header(T.files(tmp_path, "upstox", "2026-11-13")[0])["source"] == "upstox"


def test_one_file_per_hour_and_a_restart_never_overwrites(tmp_path):
    w = T.TapeWriter(tmp_path, "upstox", flush_s=0.05)
    w.write(b"a", t=_t(9, 59, 59))
    w.write(b"b", t=_t(10, 0, 1))
    w.close()
    w2 = T.TapeWriter(tmp_path, "upstox", flush_s=0.05)    # the app restarted in the same hour
    w2.write(b"c", t=_t(10, 30))
    w2.close()
    names = [p.name for p in T.files(tmp_path, "upstox", "2026-11-13")]
    assert names == ["09.tape", "10.tape", "10-1.tape"]
    assert [p for _, _, p in T.read_day(tmp_path, "upstox", "2026-11-13")] == [b"a", b"b", b"c"]


def test_a_crash_loses_only_the_cut_off_block(tmp_path):
    w = T.TapeWriter(tmp_path, "upstox", flush_s=0.01)
    for i in range(3):
        w.write(f"m{i}".encode(), t=_t(11, 0, i))
        time.sleep(0.15)                              # separate blocks
    w.close()
    f = T.files(tmp_path, "upstox", "2026-11-13")[0]
    data = f.read_bytes()
    f.write_bytes(data[:-4])                          # the last block cut off mid-write
    got = [p for _, _, p in T.read_file(f)]
    assert got == [b"m0", b"m1"]


def test_write_never_waits_for_the_disk(tmp_path):
    w = T.TapeWriter(tmp_path, "upstox", flush_s=0.2)
    t0 = time.perf_counter()
    for i in range(100_000):
        w.write(b"x" * 50, t=_t(12, 0, i * 0.001))
    assert time.perf_counter() - t0 < 2.0
    assert w.close()["records"] == 100_000


def test_low_disk_pauses_the_tape_instead_of_filling_it(tmp_path, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(T.shutil, "disk_usage", lambda p: Usage(100e9, 99e9, 1e9))
    w = T.TapeWriter(tmp_path, "upstox", flush_s=0.05, min_free_gb=5)
    w.write(b"x", t=_t(13, 0))
    st = w.close()
    assert st["paused"] and st["dropped"] == 1 and st["records"] == 0
    assert T.files(tmp_path, "upstox", "2026-11-13") == []


def test_day_summary_shows_silences_and_control_events(tmp_path):
    w = T.TapeWriter(tmp_path, "upstox", flush_s=0.05)
    w.control("subscribe", t=_t(9, 15), mode="full", keys=["NSE_INDEX|Nifty 50"])
    for s in (0, 1, 2, 300, 301):                     # a 5-minute silence after 09:15:02
        w.write(b"tick", t=_t(9, 15, s))
    w.close()
    s = T.summary(tmp_path, "upstox", "2026-11-13", gap_s=60)
    assert s["messages"] == 5 and s["files"] == 1 and s["bytes"] > 0
    assert s["controls"][0]["event"] == "subscribe" and s["controls"][0]["mode"] == "full"
    assert len(s["gaps"]) == 1 and s["gaps"][0][2] == 298.0
