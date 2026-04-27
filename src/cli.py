import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_config_cache = None
_systems_cache = None
_settings_cache = None

SETTINGS_PATH = CONFIG_DIR / "settings.json"
SETTINGS_DEFAULTS = {
    "audio_volume": 0,
    "overlay_font_size": 120,
    "overlay_mode": "fade",
    "fullscreen": True,
    "video_smooth": False,
    "integer_scale": False,
    "aspect_ratio": "auto",   # "auto" | "4:3" | "16:9" | "16:10"
    "rewind": False,
    "fast_forward": 2,        # 2 | 4 | 8 | 0 (0 = unlimited)
    "hotkeys": {
        "keyboard": "escape",
        "gamepad": "nul",
        "channel_up_keyboard": "nul",
        "channel_up_gamepad": "nul",
        "channel_down_keyboard": "nul",
        "channel_down_gamepad": "nul",
    },
    "input_mappings": {},
}


def get_config():
    global _config_cache
    if _config_cache is None:
        _config_cache = load_json(CONFIG_DIR / "config.json")
    return _config_cache


def get_systems():
    global _systems_cache
    if _systems_cache is None:
        _systems_cache = load_json(CONFIG_DIR / "systems.json")
    return _systems_cache


def get_settings():
    global _settings_cache
    if _settings_cache is None:
        try:
            _settings_cache = load_json(SETTINGS_PATH)
        except (FileNotFoundError, json.JSONDecodeError):
            _settings_cache = copy.deepcopy(SETTINGS_DEFAULTS)
        for k, v in SETTINGS_DEFAULTS.items():
            _settings_cache.setdefault(k, copy.deepcopy(v))
    return _settings_cache


def save_settings(settings):
    global _settings_cache
    _settings_cache = settings
    # write atomically via a temp file then rename
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(settings, f, indent=2)
        tmp.replace(SETTINGS_PATH)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def list_systems():
    systems = get_systems()
    print(f"\n{'System':<12} {'Name':<30} {'Core'}")
    print("-" * 70)
    for key, info in systems.items():
        print(f"{key:<12} {info['name']:<30} {info['core']}")


def list_games(system=None):
    config = get_config()
    systems = get_systems()
    roms_dir = ROOT / config["roms_dir"]

    if system and system not in systems:
        print(f"Unknown system: {system}")
        sys.exit(1)

    targets = {system: systems[system]} if system else systems

    for key, info in targets.items():
        system_dir = roms_dir / key
        if not system_dir.exists() or not system_dir.is_dir():
            continue
        exts = set(info["extensions"])
        games = sorted(
            [f for f in system_dir.iterdir() if f.suffix.lower() in exts],
            key=lambda f: f.stem.lower(),
        )
        if games:
            print(f"\n  {info['name']} ({key}/)")
            for g in games:
                print(f"    {g.stem}")
        elif system:
            print(f"\n  No games found in {key}/")


def launch(system, game):
    config = get_config()
    systems = get_systems()

    if system not in systems:
        print(f"Unknown system: {system}")
        sys.exit(1)

    info = systems[system]
    roms_dir = ROOT / config["roms_dir"] / system
    if not roms_dir.exists() or not roms_dir.is_dir():
        print(f"No ROM directory found for {system}: {roms_dir}")
        sys.exit(1)

    exts = set(info["extensions"])
    candidates = [f for f in roms_dir.iterdir() if f.suffix.lower() in exts]
    game_lower = game.lower()

    matches = [f for f in candidates if f.stem.lower() == game_lower]

    if not matches:
        matches = [f for f in candidates if game_lower in f.stem.lower()]

    if not matches:
        print(f"No game matching '{game}' found in {system}/")
        sys.exit(1)

    if len(matches) > 1:
        print("Multiple matches:")
        for m in matches:
            print(f"  {m.stem}")
        sys.exit(1)

    rom = matches[0]
    retroarch = config["retroarch_path"]
    core_name = info["core"]

    # Look for core in local cores/ dir first, then let RetroArch find it
    local_core = ROOT / config["cores_dir"] / f"{core_name}.dylib"
    if local_core.exists():
        core_path = str(local_core)
    else:
        core_path = core_name

    from src.browser import write_retroarch_config

    cfg = write_retroarch_config(system)
    cmd = [retroarch, "-L", core_path, str(rom), "--config", cfg]
    print(f"Launching {rom.stem} on {info['name']}...")
    try:
        try:
            proc = subprocess.run(cmd, check=False)
            if proc.returncode != 0:
                print(f"RetroArch exited with code {proc.returncode}")
                if proc.returncode < 0:
                    sys.exit(1)
                sys.exit(proc.returncode)
        except FileNotFoundError:
            print(f"RetroArch binary not found: {retroarch}")
            sys.exit(1)
    finally:
        try:
            os.unlink(cfg)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(
        prog="emu",
        description="RetroArch CLI wrapper",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser(
        "systems",
        aliases=["list-systems"],
        help="List supported systems",
    )

    games_parser = sub.add_parser(
        "games",
        aliases=["list-games"],
        help="List available games",
    )
    games_parser.add_argument("system", nargs="?", help="Filter by system")

    play_parser = sub.add_parser(
        "play",
        aliases=["launch"],
        help="Launch a game",
    )
    play_parser.add_argument("system", help="System (e.g. nes, snes, gba)")
    play_parser.add_argument("game", help="Game name (partial match supported)")

    args = parser.parse_args()

    if args.command in {"systems", "list-systems"}:
        list_systems()
    elif args.command in {"games", "list-games"}:
        list_games(args.system)
    elif args.command in {"play", "launch"}:
        launch(args.system, args.game)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
