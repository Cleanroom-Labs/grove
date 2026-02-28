"""
grove/filelock.py
File locking utilities for concurrent safety.

Provides advisory locking via ``fcntl.flock`` so that multiple worktrees
(or parallel grove invocations) cannot corrupt shared JSON state files.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator


@contextmanager
def locked_open(path: Path, mode: str = "r", *, shared: bool = False) -> Iterator[IO]:
    """Open *path* with an advisory flock held for the duration.

    Args:
        path: File to open.
        mode: File open mode (``'r'``, ``'w'``, ``'a'``, etc.).
        shared: If True use ``LOCK_SH`` (concurrent readers OK),
                otherwise ``LOCK_EX`` (exclusive).
    """
    lock_flag = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode) as f:
        fcntl.flock(f.fileno(), lock_flag)
        try:
            yield f
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, data: str) -> None:
    """Write *data* to *path* atomically.

    Writes to a temporary file in the same directory, then replaces the
    target via ``os.replace`` (atomic on POSIX).  A sibling ``.lock``
    file serialises concurrent writers with ``fcntl.flock``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with open(lock_path, "a") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                os.write(fd, data.encode())
                os.close(fd)
                os.replace(tmp, str(path))
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass  # already closed
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
