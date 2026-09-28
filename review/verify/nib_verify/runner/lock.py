"""Single-instance lock.

Two executors writing the same store would double-count nothing (writes are
upserts) but would waste the whole run, and the task table would flip between
states. One lock per root, not per config: the store is the thing being shared.
"""

from __future__ import annotations

import os
from pathlib import Path


class Locked(Exception):
    """Another run already holds the lock."""


class SingleInstanceLock:
    """A pid file that refuses a second holder.

    Stale locks are taken over: a process that died without cleaning up must not
    be able to block the next run forever.
    """

    def __init__(self, root: str | Path, name: str = "verify.lock") -> None:
        self.root = Path(root)
        self.path = self.root / name
        self._held = False

    def acquire(self) -> "SingleInstanceLock":
        self.root.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                other = self._read_pid()
                if other is not None and _alive(other):
                    raise Locked(
                        f"{self.path} is held by pid {other}; "
                        "another verification run is in progress"
                    ) from None
                # stale: the holder is gone
                try:
                    self.path.unlink()
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            self._held = True
            return self
        raise Locked(f"could not acquire {self.path}")

    def release(self) -> None:
        if self._held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._held = False

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()

    def _read_pid(self) -> int | None:
        try:
            return int(self.path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None


def _alive(pid: int) -> bool:
    """Best-effort liveness check, portable enough for a pid file."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _alive_windows(pid: int) -> bool:
    # os.kill(pid, 0) is not a liveness probe on Windows, so ask the OS
    # directly: open the process and see whether it has actually exited.
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
