import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_json(path):
    with open(path) as f:
        return json.load(f)


def get_config():
    return load_json(CONFIG_DIR / "config.json")


def get_systems():
    return load_json(CONFIG_DIR / "systems.json")


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

    targets = {system: systems[system]} if system else systems

    for key, info in targets.items():
        system_dir = roms_dir / key
        if not system_dir.exists():
            continue
        games = [
            f for f in system_dir.iterdir()
            if f.suffix.lower() in info["extensions"]
        ]
        if games:
            print(f"\n  {info['name']} ({key}/)")
            for g in sorted(games):
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

    matches = [
        f for f in roms_dir.iterdir()
        if f.stem.lower() == game.lower() and f.suffix.lower() in info["extensions"]
    ]

    if not matches:
        matches = [
            f for f in roms_dir.iterdir()
            if game.lower() in f.stem.lower() and f.suffix.lower() in info["extensions"]
        ]

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

    cmd = [retroarch, "-L", core_path, str(rom)]
    print(f"Launching {rom.stem} on {info['name']}...")
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(
        prog="emu",
        description="RetroArch CLI wrapper",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("systems", help="List supported systems")

    games_parser = sub.add_parser("games", help="List available games")
    games_parser.add_argument("system", nargs="?", help="Filter by system")

    play_parser = sub.add_parser("play", help="Launch a game")
    play_parser.add_argument("system", help="System (e.g. nes, snes, gba)")
    play_parser.add_argument("game", help="Game name (partial match supported)")

    args = parser.parse_args()

    if args.command == "systems":
        list_systems()
    elif args.command == "games":
        list_games(args.system)
    elif args.command == "play":
        launch(args.system, args.game)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
