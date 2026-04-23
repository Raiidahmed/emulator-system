import curses
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAVES_DIR = ROOT / "saves"

from src.cli import get_config, get_systems


# ── save state helpers ──────────────────────────────────────────────


def ensure_saves_dir(system_key):
    d = SAVES_DIR / system_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def has_save_state(system_key, rom):
    return (SAVES_DIR / system_key / f"{rom.stem}.state.auto").exists()


def write_retroarch_config(system_key):
    save_dir = ensure_saves_dir(system_key)
    content = (
        f'savestate_auto_save = "true"\n'
        f'savestate_auto_load = "true"\n'
        f'savestate_directory = "{save_dir}"\n'
        f'network_cmd_enable = "true"\n'
        f'network_cmd_port = "55355"\n'
    )
    fd, path = tempfile.mkstemp(suffix=".cfg", prefix="emu_")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def send_cmd(cmd, port=55355):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(cmd.encode(), ("127.0.0.1", port))
        sock.close()
    except OSError:
        pass


def save_last_played(system_key, rom):
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    with open(SAVES_DIR / ".last_played.json", "w") as f:
        json.dump({"system": system_key, "rom": str(rom), "name": rom.stem}, f)


def load_last_played():
    try:
        with open(SAVES_DIR / ".last_played.json") as f:
            data = json.load(f)
        rom = Path(data["rom"])
        if rom.exists() and has_save_state(data["system"], rom):
            return data
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None


# ── active game state ───────────────────────────────────────────────

_active = {"proc": None, "system": None, "rom": None, "cfg": None}


def _cleanup_active():
    if _active["cfg"]:
        try:
            os.unlink(_active["cfg"])
        except OSError:
            pass
    _active["proc"] = None
    _active["system"] = None
    _active["rom"] = None
    _active["cfg"] = None


def _reap_if_exited():
    if _active["proc"] and _active["proc"].poll() is not None:
        _cleanup_active()


def stop_current_game():
    if _active["proc"] is None:
        return
    if _active["proc"].poll() is None:
        send_cmd("SAVE_STATE")
        time.sleep(0.3)
        _active["proc"].terminate()
        try:
            _active["proc"].wait(timeout=2)
        except subprocess.TimeoutExpired:
            _active["proc"].kill()
            _active["proc"].wait()
    _cleanup_active()


def start_game(rom, system_key, system_info):
    # skip if this exact game is already running
    if (_active["proc"] and _active["proc"].poll() is None
            and _active["rom"] == rom):
        return

    config = get_config()
    core_name = system_info["core"]
    local_core = ROOT / config["cores_dir"] / f"{core_name}.dylib"
    core_path = str(local_core) if local_core.exists() else core_name
    retroarch = config["retroarch_path"]

    stop_current_game()

    cfg = write_retroarch_config(system_key)
    proc = subprocess.Popen(
        [retroarch, "-L", core_path, str(rom), "--appendconfig", cfg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    _active["proc"] = proc
    _active["system"] = system_key
    _active["rom"] = rom
    _active["cfg"] = cfg
    save_last_played(system_key, rom)


# ── game scanning ───────────────────────────────────────────────────


def scan_games(system_key, system_info):
    config = get_config()
    roms_dir = ROOT / config["roms_dir"] / system_key
    if not roms_dir.exists():
        return []
    return sorted(
        [f for f in roms_dir.iterdir() if f.suffix.lower() in system_info["extensions"]],
        key=lambda f: f.stem.lower(),
    )


# ── TUI ─────────────────────────────────────────────────────────────


def draw_loading(stdscr, game_name, frame):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    spinner = "\u28bf\u28bb\u28fb\u28f9\u28fd\u28fc\u28fe\u287f"
    char = spinner[frame % len(spinner)]

    msg = f"{char} Loading {game_name}..."
    row = h // 2
    col = max(0, (w - len(msg)) // 2)
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(row, col, msg, w - 2)
    stdscr.attroff(curses.A_BOLD)

    stdscr.refresh()


def launch_with_loading(stdscr, rom, system_key, system_info):
    done = threading.Event()

    def _launch():
        start_game(rom, system_key, system_info)
        done.set()

    thread = threading.Thread(target=_launch, daemon=True)
    thread.start()

    stdscr.timeout(80)
    frame = 0
    while not done.is_set():
        draw_loading(stdscr, rom.stem, frame)
        frame += 1
        stdscr.getch()  # drives the timeout refresh
    stdscr.timeout(1000)


def draw(stdscr, path_parts, items, cursor, empty_msg, now_playing=None):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    # breadcrumb
    crumb = " > ".join(["emu"] + path_parts)
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(1, 2, crumb, w - 4)
    stdscr.attroff(curses.A_BOLD)

    # divider
    stdscr.addnstr(2, 2, "\u2500" * min(len(crumb), w - 4), w - 4)

    # now-playing banner
    list_start = 4
    if now_playing:
        stdscr.addnstr(3, 4, f"\u25b6 Now playing: {now_playing}", w - 6, curses.A_DIM)
        list_start = 5

    if not items:
        stdscr.addnstr(list_start, 4, empty_msg, w - 6, curses.A_DIM)
    else:
        visible = h - list_start - 3
        if visible < 1:
            visible = 1

        if cursor < visible:
            offset = 0
        else:
            offset = cursor - visible + 1

        for i, (label, _) in enumerate(items[offset : offset + visible]):
            row = list_start + i
            if row >= h - 2:
                break
            if i + offset == cursor:
                stdscr.attron(curses.A_REVERSE)
                stdscr.addnstr(row, 3, f" {label} ", w - 5)
                stdscr.attroff(curses.A_REVERSE)
            else:
                stdscr.addnstr(row, 4, label, w - 6)

        if len(items) > visible:
            indicator = f" {cursor + 1}/{len(items)} "
            stdscr.addnstr(
                2, max(2, w - len(indicator) - 2), indicator, w - 4, curses.A_DIM
            )

    # footer
    footer_row = h - 1
    if path_parts:
        hint = " [\u2191\u2193] navigate  [enter] play  [esc] back  [q] quit "
    else:
        hint = " [\u2191\u2193] navigate  [enter] open  [q] quit "
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(footer_row, 2, hint, w - 4)
    stdscr.attroff(curses.A_DIM)

    stdscr.refresh()


def run(stdscr):
    curses.curs_set(0)
    curses.use_default_colors()
    stdscr.timeout(1000)

    systems = get_systems()

    level = "systems"
    current_system = None
    cursor = 0

    while True:
        _reap_if_exited()

        now_playing = _active["rom"].stem if _active["rom"] else None

        if level == "systems":
            items = []
            last = load_last_played()
            if last and last["system"] in systems:
                items.append(
                    (f"\u25b8 Resume: {last['name']}", ("resume", last))
                )
            for key, info in systems.items():
                games = scan_games(key, info)
                if games:
                    items.append((f"{info['name']}  ({len(games)})", key))
            draw(
                stdscr, [], items, cursor,
                "No ROMs found. Add games to roms/<system>/",
                now_playing,
            )
        else:
            info = systems[current_system]
            games = scan_games(current_system, info)
            items = []
            for g in games:
                prefix = "\u25cf " if has_save_state(current_system, g) else "  "
                items.append((f"{prefix}{g.stem}", g))
            draw(
                stdscr, [current_system], items, cursor,
                "No games in this folder.",
                now_playing,
            )

        key = stdscr.getch()

        if key == -1:
            continue

        if key == ord("q") or key == ord("Q"):
            stop_current_game()
            break

        elif key == curses.KEY_UP or key == ord("k"):
            if items:
                cursor = (cursor - 1) % len(items)

        elif key == curses.KEY_DOWN or key == ord("j"):
            if items:
                cursor = (cursor + 1) % len(items)

        elif key in (curses.KEY_ENTER, 10, 13):
            if not items:
                continue
            selected = items[cursor][1]

            if level == "systems":
                if isinstance(selected, tuple) and selected[0] == "resume":
                    last = selected[1]
                    rom = Path(last["rom"])
                    sys_key = last["system"]
                    launch_with_loading(stdscr, rom, sys_key, systems[sys_key])
                else:
                    current_system = selected
                    level = "games"
                    cursor = 0
            else:
                rom = selected
                launch_with_loading(stdscr, rom, current_system, systems[current_system])

        elif key == 27 or key == curses.KEY_BACKSPACE or key == curses.KEY_LEFT:
            if level == "games":
                level = "systems"
                current_system = None
                cursor = 0
            else:
                stop_current_game()
                break


def main():
    curses.wrapper(run)
