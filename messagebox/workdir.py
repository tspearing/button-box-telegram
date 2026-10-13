"""Directories that must not depend on the process's starting folder.

lgpio creates a notify pipe (``.lgd-nfy*``) in ``LG_WD`` or, if that is unset,
in the current working directory. The service user cannot write to
``/opt/message-box``, so the pipe has to live under the data directory.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def absolute_data_dir(path: Path) -> Path:
    """Resolve ``path`` against the current directory when it is relative."""
    directory = Path(path).expanduser()
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    return directory.resolve()


def ensure_writable_data_dir(preferred: Path) -> Path:
    """Return a directory the process can create files in.

    The preferred path is used when it can be created and written. Otherwise
    a directory under the system temp folder is used. The result is always
    absolute, so later ``chdir`` calls cannot move the queue or a test
    recording back onto the folder the command was started from.
    """
    directory = absolute_data_dir(preferred)
    if _can_write(directory):
        return directory
    fallback = Path(tempfile.gettempdir()) / f"message-box-{os.getuid()}"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve()


def prepare_lgpio_directory(preferred: Path) -> Path:
    """Point lgpio at ``preferred`` before gpiozero or lgpio is imported.

    ``LG_WD`` is read the first time the library creates its pipe, so this
    must run before that import. The process also changes directory so a
    build of lgpio that ignores ``LG_WD`` still writes the pipe somewhere
    writable.
    """
    directory = ensure_writable_data_dir(preferred)
    os.environ["LG_WD"] = str(directory)
    os.chdir(directory)
    return directory


def _can_write(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True
