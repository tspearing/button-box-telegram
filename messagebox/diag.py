"""Try one announcement without restarting the service.

    python -m messagebox.diag announce "German" --lead-in-mode noise --lead-in-ms 850
    python -m messagebox.diag announce "German" --lead-in-mode silence --lead-in-ms 850
    python -m messagebox.diag announce "German" --no-play --write /tmp/german.wav
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from messagebox.announce import build_announcer
from messagebox.audio import AudioEngine, playback_command
from messagebox.config import load_config, resolve_config_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="messagebox.diag",
        description="Play or save one card-name announcement.",
    )
    parser.add_argument("--config", type=Path, help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    announce = sub.add_parser("announce", help="speak one phrase through the speaker")
    announce.add_argument("phrase", help='words to speak, for example "German"')
    announce.add_argument("--lead-in-mode", choices=("noise", "silence"))
    announce.add_argument("--lead-in-ms", type=int)
    announce.add_argument("--lead-in-level-db", type=float)
    announce.add_argument(
        "--write",
        type=Path,
        help="copy the spoken clip here, without the playback lead-in",
    )
    announce.add_argument(
        "--render",
        type=Path,
        help="write the full speaker stream, including the lead-in, and do not play",
    )
    announce.add_argument(
        "--no-play",
        action="store_true",
        help="do not open the speaker",
    )
    args = parser.parse_args(argv)
    if args.command != "announce":
        parser.error(f"unknown command {args.command}")
        return 2
    try:
        path = resolve_config_path(args.config, dev=False)
        config = load_config(path, dev=False)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    audio = config.audio
    if args.lead_in_mode is not None:
        audio = _replace_audio(audio, lead_in_mode=args.lead_in_mode)
    if args.lead_in_ms is not None:
        audio = _replace_audio(audio, lead_in_ms=args.lead_in_ms)
    if args.lead_in_level_db is not None:
        audio = _replace_audio(audio, lead_in_level_db=args.lead_in_level_db)
    with tempfile.TemporaryDirectory(prefix="messagebox-diag-") as folder:
        cache = Path(folder)
        announcer = build_announcer(
            cache,
            engine=config.announce.engine,
            piper_voice=config.announce.piper_voice,
            piper_length_scale=config.announce.piper_length_scale,
            sample_rate=audio.sample_rate,
            lead_in_mode=audio.lead_in_mode,
            lead_in_level_db=audio.lead_in_level_db,
        )
        clip = announcer.clip_for(args.phrase, None)
        if clip is None:
            print("Could not prepare the announcement. See the messages above.", file=sys.stderr)
            return 1
        print(f"clip: {clip}")
        print(
            f"lead-in: {audio.lead_in_mode} {audio.lead_in_ms} ms "
            f"at {audio.lead_in_level_db} dB"
        )
        if args.write is not None:
            args.write.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(clip, args.write)
            print(f"wrote {args.write}")
        engine = AudioEngine(audio)
        if args.render is not None:
            args.render.parent.mkdir(parents=True, exist_ok=True)
            command = playback_command(
                clip,
                "none",
                lead_in_ms=audio.lead_in_ms,
                lead_in_mode=audio.lead_in_mode,
                lead_in_level_db=audio.lead_in_level_db,
                destination=args.render,
            )
            completed = subprocess.run(command, check=False, capture_output=True)
            if completed.returncode != 0:
                print(completed.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
                return 1
            print(f"rendered {args.render}")
            return 0
        if args.no_play:
            return 0
        command = engine.play_cmd(clip)
        completed = subprocess.run(command, check=False)
        return 0 if completed.returncode == 0 else 1


def _replace_audio(audio, **changes):
    from dataclasses import replace

    return replace(audio, **changes)


if __name__ == "__main__":
    raise SystemExit(main())
