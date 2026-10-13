"""Data files and the lgpio pipe must not follow the launch directory."""

import os
from pathlib import Path

from messagebox.workdir import (
    absolute_data_dir,
    ensure_writable_data_dir,
    prepare_lgpio_directory,
)


def test_unwritable_directory_falls_back_to_an_absolute_temp_dir(tmp_path, monkeypatch):
    preferred = tmp_path / "denied"
    blocked = preferred.resolve()
    original = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self == preferred or self.resolve() == blocked:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.chdir(tmp_path)
    chosen = ensure_writable_data_dir(preferred)
    assert chosen.is_absolute()
    assert chosen != preferred
    assert chosen.name == f"message-box-{os.getuid()}"
    assert os.access(chosen, os.W_OK)


def test_relative_data_dir_is_resolved_before_any_later_chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = absolute_data_dir(Path("data"))
    assert resolved == (tmp_path / "data").resolve()
    assert resolved.is_absolute()


def test_lgpio_directory_is_absolute_and_becomes_the_cwd(tmp_path, monkeypatch):
    start = Path.cwd()
    monkeypatch.delenv("LG_WD", raising=False)
    try:
        directory = prepare_lgpio_directory(tmp_path / "state")
        assert directory.is_absolute()
        assert Path(os.environ["LG_WD"]) == directory
        assert Path.cwd() == directory
        assert (directory / ".write-test").exists() is False
    finally:
        os.chdir(start)
