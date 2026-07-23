from pathlib import Path

import pytest

from core.instance import single_bot_instance


def test_single_bot_instance_rejects_second_process_lock(
    tmp_path: Path, monkeypatch
) -> None:
    lock_path = tmp_path / "bot.pid"
    lock_path.write_text("12345", encoding="ascii")
    monkeypatch.setattr("core.instance._process_is_running", lambda pid: pid == 12345)

    with pytest.raises(RuntimeError, match="уже запущено"):
        with single_bot_instance(lock_path):
            pass


def test_single_bot_instance_replaces_stale_lock(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / "bot.pid"
    lock_path.write_text("12345", encoding="ascii")
    monkeypatch.setattr("core.instance._process_is_running", lambda pid: False)

    with single_bot_instance(lock_path):
        assert lock_path.exists()

    assert not lock_path.exists()
