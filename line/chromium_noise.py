import contextlib
import ctypes
import msvcrt
import os
import re
import sys
import threading
from collections.abc import Iterator
from typing import BinaryIO

HARMLESS = (
    re.compile(rb"direct_composition_support\.cc:\d+\] QueryVideoProcessorCustomExtForHDR: "
               rb"Failed to retrieve D3D11 device\r?\n$"),
)
STD_ERROR_HANDLE = -12
SECONDS_TO_PASS_ON_THE_LAST_LINES = 5
_as_started: list[int] = []


def stderr_as_started() -> int | None:
    return _as_started[-1] if _as_started else None


def pass_on(lines: BinaryIO, passed_on: BinaryIO) -> None:
    for line in lines:
        if not any(harmless.search(line) for harmless in HARMLESS):
            passed_on.write(line)
            passed_on.flush()


def _make_fd_2_the_handle_children_inherit() -> None:
    ctypes.windll.kernel32.SetStdHandle(STD_ERROR_HANDLE, msvcrt.get_osfhandle(2))


@contextlib.contextmanager
def left_out_of_stderr() -> Iterator[None]:
    try:
        stderr = os.dup(2)
    except OSError:
        yield
        return
    read_end, write_end = os.pipe()
    os.dup2(write_end, 2)
    os.close(write_end)
    _make_fd_2_the_handle_children_inherit()
    passing_on = threading.Thread(target=pass_on, args=(os.fdopen(read_end, "rb"), os.fdopen(os.dup(stderr), "wb")),
                                  name="chromium_noise", daemon=True)
    passing_on.start()
    _as_started.append(stderr)
    try:
        yield
    finally:
        _as_started.remove(stderr)
        sys.stderr.flush()
        os.dup2(stderr, 2)
        os.close(stderr)
        _make_fd_2_the_handle_children_inherit()
        passing_on.join(SECONDS_TO_PASS_ON_THE_LAST_LINES)
