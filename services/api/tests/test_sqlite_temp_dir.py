"""不可用的系统临时目录不能阻断 SQLite 大字段写入。"""
import os
import sqlite3

from app.db import _configure_sqlite_temp_dir


def test_sqlite_temp_directory_is_anchored_to_database(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_TMPDIR", str(tmp_path / "missing"))
    monkeypatch.setenv("TMP", str(tmp_path / "missing"))
    monkeypatch.setenv("TEMP", str(tmp_path / "missing"))
    database = tmp_path / "configured" / "data" / "aiedu.db"
    elsewhere = tmp_path / "launch-directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _configure_sqlite_temp_dir("sqlite:///" + database.as_posix())
    expected = database.parent / ".sqlite-tmp"
    assert expected.is_dir()
    assert os.environ["SQLITE_TMPDIR"] == str(expected)
    if os.name == "nt":
        assert os.environ["TMP"] == os.environ["TEMP"] == str(expected)
    assert not database.exists()


def test_sqlite_large_temp_write_with_unavailable_system_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_TMPDIR", str(tmp_path / "missing"))
    monkeypatch.setenv("TMP", str(tmp_path / "missing"))
    monkeypatch.setenv("TEMP", str(tmp_path / "missing"))
    _configure_sqlite_temp_dir("sqlite:///" + (tmp_path / "local.db").as_posix())
    connection = sqlite3.connect("")
    try:
        connection.execute("CREATE TABLE probe (id TEXT PRIMARY KEY, payload TEXT)")
        connection.execute("INSERT INTO probe VALUES (?, ?)", ("sample", "x" * 8_000_000))
        connection.commit()
        assert connection.execute("SELECT length(payload) FROM probe").fetchone()[0] == 8_000_000
    finally:
        connection.close()
