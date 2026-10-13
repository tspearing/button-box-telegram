"""Load config.toml and the bot token.

The token is never stored in the config file. systemd injects it from
``/etc/message-box/env`` (root, mode 600) before the service drops
privileges. Dev mode can use a local ``.env`` instead.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_DATA_DIR = Path("/var/lib/message-box")
DEFAULT_ENV_FILE = Path("/etc/message-box/env")
SYSTEM_CONFIG_PATH = Path("/etc/message-box/config.toml")


@dataclass(frozen=True)
class ButtonConfig:
    pin: int = 17
    pull_up: bool = True
    bounce_seconds: float = 0.05
    hold_seconds: float = 0.6
    min_record_seconds: float = 1.0
    max_record_seconds: float = 120.0
    debounce_seconds: float = 0.03


@dataclass(frozen=True)
class LampConfig:
    enabled: bool = True
    pin: int = 26
    active_high: bool = True
    waiting: str = "steady"
    waiting_on_seconds: float = 0.15
    waiting_off_seconds: float = 1.6
    recording_on_seconds: float = 0.1
    recording_off_seconds: float = 0.1


@dataclass(frozen=True)
class AudioConfig:
    mic_device: str = "auto"
    speaker_device: str = "auto"
    mic_name_hints: tuple[str, ...] = ("TONOR", "G11", "USB")
    speaker_name_hints: tuple[str, ...] = ("LIELONGREN", "USB Audio", "USB")
    sample_rate: int = 48000
    channels: int = 1
    opus_bitrate: str = "32k"
    play_empty_tone: bool = False


@dataclass(frozen=True)
class TelegramConfig:
    allowed_chat_ids: tuple[int, ...] = ()
    family_chat_id: int | None = None


@dataclass(frozen=True)
class PathsConfig:
    data_dir: Path = DEFAULT_DATA_DIR
    env_file: Path = DEFAULT_ENV_FILE


@dataclass(frozen=True)
class RecipientConfig:
    idle_timeout_seconds: float = 120.0


@dataclass(frozen=True)
class DevConfig:
    repeat_window_seconds: float = 0.7
    repeat_gap_seconds: float = 0.22


@dataclass(frozen=True)
class AppConfig:
    button: ButtonConfig = field(default_factory=ButtonConfig)
    lamp: LampConfig = field(default_factory=LampConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    recipient: RecipientConfig = field(default_factory=RecipientConfig)
    dev_keys: DevConfig = field(default_factory=DevConfig)
    log_level: str = "INFO"
    dev_mode: bool = False


def parse_env_text(text: str) -> dict[str, str]:
    """Parse a systemd EnvironmentFile / .env body. No shell expansion."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def normalize_token(token: str) -> str:
    """Strip quotes and an optional ``bot`` prefix copied from a URL."""
    token = token.strip().strip('"').strip("'").strip()
    if token.lower().startswith("bot") and ":" in token[3:]:
        rest = token[3:]
        head, _, _tail = rest.partition(":")
        if head.isdigit():
            return rest
    return token


def load_token(*candidates: Path) -> str:
    """Return the bot token from the environment, then from the first readable file.

    Existing environment variables win, which is how systemd passes the
    root-only env file into the unprivileged service.
    """
    existing = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if existing:
        return normalize_token(existing)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found = parse_env_text(text).get("TELEGRAM_BOT_TOKEN", "").strip()
        if found:
            return normalize_token(found)
    return ""


def resolve_config_path(explicit: Path | None, *, dev: bool) -> Path | None:
    """Pick a config file. None means "built-in defaults".

    A ``config.toml`` in the current directory is used only when this process
    can open it. An unreadable file (common when the service user is started
    from someone else's home directory) is skipped so the system file can be
    used instead. Commands therefore work from any directory.
    """
    if explicit is not None:
        return explicit
    env = os.environ.get("MESSAGEBOX_CONFIG", "").strip()
    if env:
        return Path(env)
    local = Path("config.toml")
    if _openable_file(local):
        return local
    if _present_but_unreadable(local):
        print(
            f"Ignoring {local.resolve()} because it is not readable. "
            f"Trying {SYSTEM_CONFIG_PATH}.",
            file=sys.stderr,
        )
    system = SYSTEM_CONFIG_PATH
    if dev:
        example = Path("config.example.toml")
        if _openable_file(example) and not _openable_file(system):
            return example
        if _openable_file(system):
            return system
        if _openable_file(example):
            return example
        return None
    if _openable_file(system):
        return system
    example = Path("config.example.toml")
    if _openable_file(example):
        return example
    return None


def _openable_file(path: Path) -> bool:
    """True when ``path`` is a regular file this process can open."""
    try:
        if not path.is_file():
            return False
        with path.open("rb"):
            return True
    except OSError:
        return False


def _present_but_unreadable(path: Path) -> bool:
    """True when ``path`` exists as a file but opening it fails."""
    try:
        if not path.is_file():
            return False
    except OSError:
        return True
    return not _openable_file(path)


def load_config(path: Path | None, *, dev: bool = False) -> AppConfig:
    data: dict = {}
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(path)
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must be a TOML table")
        data = loaded
    config = _from_tables(data, dev_mode=dev)
    _validate(config)
    return _apply_runtime_overrides(config, dev=dev)


def resolved_family_chat_id(config: AppConfig) -> int | None:
    """The chat outgoing notes are sent to.

    When the allowlist has exactly one chat and ``family_chat_id`` is omitted,
    that chat is the family. A family id outside a non-empty allowlist is an
    error: the box must not send somewhere it would refuse to hear.
    """
    allowed = config.telegram.allowed_chat_ids
    family = config.telegram.family_chat_id
    if family is None and len(allowed) == 1:
        family = allowed[0]
    if family is not None and allowed and family not in allowed:
        raise ValueError("family_chat_id must be one of allowed_chat_ids")
    if family == 0:
        return None
    return family


def _apply_runtime_overrides(config: AppConfig, *, dev: bool) -> AppConfig:
    mic = os.environ.get("MESSAGEBOX_MIC", "").strip()
    speaker = os.environ.get("MESSAGEBOX_SPEAKER", "").strip()
    data = os.environ.get("MESSAGEBOX_DATA_DIR", "").strip()
    audio = config.audio
    paths = config.paths
    if mic:
        audio = _replace(audio, mic_device=mic)
    if speaker:
        audio = _replace(audio, speaker_device=speaker)
    if data:
        paths = _replace(paths, data_dir=Path(data))
    elif dev and paths.data_dir == DEFAULT_DATA_DIR and not _writable_dir(paths.data_dir):
        paths = _replace(paths, data_dir=Path("data"))
    if audio is config.audio and paths is config.paths:
        return config
    return _replace(config, audio=audio, paths=paths)


def _writable_dir(path: Path) -> bool:
    """True when a dev-mode process could store notes in ``path``.

    This must not create the directory. Checking the parent is enough to
    decide whether to fall back to ``./data`` on a laptop.
    """
    if path.exists():
        return os.access(path, os.W_OK)
    parent = path.parent if path.parent != path else path
    return parent.exists() and os.access(parent, os.W_OK)


def _from_tables(data: dict, *, dev_mode: bool) -> AppConfig:
    button = data.get("button") or {}
    lamp = data.get("lamp") or {}
    audio = data.get("audio") or {}
    telegram = data.get("telegram") or {}
    paths = data.get("paths") or {}
    recipient = data.get("recipient") or {}
    dev_keys = data.get("dev") or {}
    logging_table = data.get("logging") or {}
    family = telegram.get("family_chat_id", None)
    if family is not None:
        family = int(family)
        if family == 0:
            family = None
    allowed = tuple(int(item) for item in telegram.get("allowed_chat_ids", []))
    return AppConfig(
        button=ButtonConfig(
            pin=int(button.get("pin", 17)),
            pull_up=bool(button.get("pull_up", True)),
            bounce_seconds=float(button.get("bounce_seconds", 0.05)),
            hold_seconds=float(button.get("hold_seconds", 0.6)),
            min_record_seconds=float(button.get("min_record_seconds", 1.0)),
            max_record_seconds=float(button.get("max_record_seconds", 120)),
            debounce_seconds=float(button.get("debounce_seconds", 0.03)),
        ),
        lamp=LampConfig(
            enabled=bool(lamp.get("enabled", True)),
            pin=int(lamp.get("pin", 26)),
            active_high=bool(lamp.get("active_high", True)),
            waiting=str(lamp.get("waiting", "steady")),
            waiting_on_seconds=float(lamp.get("waiting_on_seconds", 0.15)),
            waiting_off_seconds=float(lamp.get("waiting_off_seconds", 1.6)),
            recording_on_seconds=float(lamp.get("recording_on_seconds", 0.1)),
            recording_off_seconds=float(lamp.get("recording_off_seconds", 0.1)),
        ),
        audio=AudioConfig(
            mic_device=str(audio.get("mic_device", "auto")),
            speaker_device=str(audio.get("speaker_device", "auto")),
            mic_name_hints=tuple(audio.get("mic_name_hints", ["TONOR", "G11", "USB"])),
            speaker_name_hints=tuple(
                audio.get("speaker_name_hints", ["LIELONGREN", "USB Audio", "USB"])
            ),
            sample_rate=int(audio.get("sample_rate", 48000)),
            channels=int(audio.get("channels", 1)),
            opus_bitrate=str(audio.get("opus_bitrate", "32k")),
            play_empty_tone=bool(audio.get("play_empty_tone", False)),
        ),
        telegram=TelegramConfig(allowed_chat_ids=allowed, family_chat_id=family),
        paths=PathsConfig(
            data_dir=Path(paths.get("data_dir", str(DEFAULT_DATA_DIR))),
            env_file=Path(paths.get("env_file", str(DEFAULT_ENV_FILE))),
        ),
        recipient=RecipientConfig(
            idle_timeout_seconds=float(recipient.get("idle_timeout_seconds", 120))
        ),
        dev_keys=DevConfig(
            repeat_window_seconds=float(dev_keys.get("repeat_window_seconds", 0.7)),
            repeat_gap_seconds=float(dev_keys.get("repeat_gap_seconds", 0.22)),
        ),
        log_level=str(logging_table.get("level", "INFO")).upper(),
        dev_mode=dev_mode,
    )


def _validate(config: AppConfig) -> None:
    if config.button.pin == config.lamp.pin and config.lamp.enabled:
        raise ValueError("button pin and lamp pin must be different")
    for pin, name in ((config.button.pin, "button"), (config.lamp.pin, "lamp")):
        if not 0 <= pin <= 27:
            raise ValueError(f"{name} pin must be a BCM number from 0 to 27")
    if config.lamp.waiting not in {"steady", "blink"}:
        raise ValueError("lamp.waiting must be 'steady' or 'blink'")
    if config.audio.sample_rate < 8000:
        raise ValueError("sample_rate is too low")
    if config.audio.channels not in {1, 2}:
        raise ValueError("channels must be 1 or 2")
    if config.button.hold_seconds <= 0 or config.button.min_record_seconds <= 0:
        raise ValueError("button timings must be positive")
    if config.button.max_record_seconds <= config.button.min_record_seconds:
        raise ValueError("max_record_seconds must be longer than min_record_seconds")
    if config.button.debounce_seconds < 0:
        raise ValueError("debounce_seconds cannot be negative")
    if config.button.debounce_seconds >= config.button.hold_seconds:
        raise ValueError("debounce_seconds must be shorter than the hold threshold")


def _replace(obj, **changes):
    from dataclasses import replace

    return replace(obj, **changes)
