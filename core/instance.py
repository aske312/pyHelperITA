from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psutil


def _process_is_running(pid: int) -> bool:
    if pid <= 0 or not psutil.pid_exists(pid):
        return False
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, OSError):
        return False


@contextmanager
def single_bot_instance(lock_path: Path = Path(".temp/bot.pid")):
    """Prevent two polling processes from using the same Telegram token."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    current_pid = os.getpid()

    while True:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                running_pid = int(lock_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                running_pid = 0
            if _process_is_running(running_pid):
                raise RuntimeError(
                    f"Приложение уже запущено (PID {running_pid}). "
                    "Остановите текущий экземпляр перед повторным запуском."
                ) from None
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as lock_file:
                lock_file.write(str(current_pid))
            break

    try:
        yield
    finally:
        try:
            if lock_path.read_text(encoding="ascii").strip() == str(current_pid):
                lock_path.unlink()
        except FileNotFoundError:
            pass
