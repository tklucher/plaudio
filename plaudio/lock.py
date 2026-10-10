"""Stops two syncs (e.g. the app and a scheduled task) from running at once."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_instance(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:  # BlockingIOError on POSIX, PermissionError on Windows
            raise AlreadyRunning("Another plaudio sync is already running.") from exc
        yield
    finally:
        fh.close()  # closing the handle releases the lock on both platforms
