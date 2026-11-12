"""Settings load order, structured logs (and secret masking), the doctor and the command line."""
import json

import pytest
from pydantic import ValidationError

from tanu import __main__ as cli
from tanu import doctor
from tanu.core import config, log


def test_settings_defaults_then_local_then_env(tmp_path):
    defaults = tmp_path / "settings.json"
    data = tmp_path / "d"
    data.mkdir()
    defaults.write_text(json.dumps({"about": "x", "data_dir": str(data), "log_level": "info"}))
    s = config.load(defaults, env={})
    assert s.log_level == "INFO" and s.data_path == data and s.log_keep_days == 30
    (data / "local.json").write_text(json.dumps({"log_keep_days": 7}))
    assert config.load(defaults, env={}).log_keep_days == 7
    assert config.load(defaults, env={"TANU_LOG_LEVEL": "debug"}).log_level == "DEBUG"


def test_settings_reject_typos_and_bad_values(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"data_dir": str(tmp_path), "log_levl": "INFO"}))
    with pytest.raises(ValidationError):
        config.load(f, env={})
    f.write_text(json.dumps({"data_dir": str(tmp_path), "log_level": "LOUD"}))
    with pytest.raises(ValidationError):
        config.load(f, env={})


def test_committed_settings_are_valid():
    s = config.load(env={})
    assert s.timezone == "Asia/Kolkata"


def test_structured_log_lines_mask_secrets(tmp_path):
    log.setup(tmp_path, "INFO", 30)
    lg = log.get("feed")
    lg.info("connected", instruments=449, access_token="abc123", api_key="k")
    lg.debug("not_written")
    files = list(tmp_path.glob("tanu-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(x) for x in files[0].read_text().splitlines()]
    assert [r["event"] for r in rows] == ["connected"]
    r = rows[0]
    assert r["comp"] == "feed" and r["run"] == log.RUN_ID and r["instruments"] == 449
    assert r["access_token"] == "***" and r["api_key"] == "***" and "abc123" not in files[0].read_text()


def test_doctor_reports_each_check(tmp_path, monkeypatch):
    monkeypatch.setenv("TANU_DATA_DIR", str(tmp_path))
    rows = doctor.checks()
    names = [r["check"] for r in rows]
    assert names[:2] == ["python", "settings"] and "data folder" in names and "disk" in names
    assert all(r["status"] in ("ok", "warn", "fail") for r in rows)
    assert (tmp_path / "logs").is_dir() and (tmp_path / "tape").is_dir()


def test_cli(capsys):
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip().count(".") == 2
    assert cli.main(["nonsense"]) == 2
