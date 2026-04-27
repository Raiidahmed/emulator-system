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

from src.cli import get_config, get_systems, get_settings, save_settings


# ── save state helpers ──────────────────────────────────────────────


def ensure_saves_dir(system_key):
    d = SAVES_DIR / system_key
    d.mkdir(parents=True, exist_ok=True)
    return d


_save_state_cache = {}
_SAVE_STATE_TTL = 5  # seconds


def has_save_state(system_key, rom):
    key = (system_key, rom.stem)
    now = time.monotonic()
    cached = _save_state_cache.get(key)
    if cached and (now - cached[0]) < _SAVE_STATE_TTL:
        return cached[1]
    result = (SAVES_DIR / system_key / f"{rom.stem}.state.auto").exists()
    _save_state_cache[key] = (now, result)
    return result


def _retroarch_base_config():
    """Read the user's RetroArch config as a dict of key=value pairs."""
    for candidate in [
        Path.home() / ".config/retroarch/retroarch.cfg",
        Path("/etc/retroarch.cfg"),
    ]:
        if candidate.exists():
            lines = {}
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        lines[k.strip()] = v.strip()
            return lines
    return {}


def write_retroarch_config(system_key, channel=None):
    save_dir = ensure_saves_dir(system_key)
    settings = get_settings()

    # start from the user's base RetroArch config
    base = _retroarch_base_config()

    # apply our overrides
    overrides = {
        "savestate_auto_save": '"true"',
        "savestate_auto_load": '"true"',
        "savestate_directory": f'"{save_dir}"',
        "network_cmd_enable": '"true"',
        "network_cmd_port": '"55355"',
        "audio_volume": f'"{settings["audio_volume"]:.1f}"',
        "config_save_on_exit": '"false"',
        # composite CRT output via Pi VideoCore — always fullscreen on Pi
        "video_driver": '"dispmanx"',
        "video_fullscreen": '"true"',
        "video_vsync": '"true"',
        "video_smooth": '"false"',
        "video_aspect_ratio_auto": '"false"',
        "video_aspect_ratio": '"1.333333"',
        "video_scale_integer": '"false"',
        "video_refresh_rate": '"60.0"',
        "audio_driver": '"alsa"',
        "audio_device": '"hw:0,0"',
    }

    # hotkey: return to TUI by quitting RetroArch
    # input_enable_hotkey = nul means no modifier needed
    hotkeys = settings.get("hotkeys", {})
    mappings_cfg_for_hotkey = settings.get("input_mappings", {}).get(system_key, {})
    device_type_for_hotkey = mappings_cfg_for_hotkey.get("device", "keyboard")
    if device_type_for_hotkey == "keyboard":
        menu_key = hotkeys.get("keyboard", "escape")
        if menu_key and menu_key != "nul":
            overrides["input_enable_hotkey"] = '"nul"'
            overrides["input_exit_emulator"] = f'"{menu_key}"'
    else:
        menu_btn = hotkeys.get("gamepad", "nul")
        if menu_btn and menu_btn != "nul":
            overrides["input_enable_hotkey_btn"] = '"nul"'
            overrides["input_exit_emulator_btn"] = f'"{menu_btn}"'

    overlay_mode = settings.get("overlay_mode", "fade")
    if channel is not None and overlay_mode != "off":
        overlay_cfg = OVERLAY_DIR / f"ch_{channel:02d}.cfg"
        if overlay_cfg.exists():
            overrides["input_overlay_enable"] = '"true"'
            overrides["input_overlay"] = f'"{overlay_cfg}"'
            overrides["input_overlay_opacity"] = '"1.0"'
            overrides["input_overlay_scale"] = '"1.0"'
            overrides["input_overlay_show_inputs"] = '"0"'
    else:
        overrides["input_overlay_enable"] = '"false"'

    # inject input mappings for this system
    mappings_cfg = settings.get("input_mappings", {}).get(system_key, {})
    device_type = mappings_cfg.get("device", "keyboard")
    btn_map = mappings_cfg.get(device_type, {})
    if btn_map:
        overrides["input_autodetect_enable"] = '"false"'
        for btn, val in btn_map.items():
            if device_type == "keyboard":
                overrides[f"input_player1_{btn}"] = f'"{val}"'
                overrides[f"input_player1_{btn}_btn"] = '"nul"'
                overrides[f"input_player1_{btn}_axis"] = '"nul"'
            else:
                overrides[f"input_player1_{btn}_btn"] = f'"{val}"'

    base.update(overrides)

    fd, path = tempfile.mkstemp(suffix=".cfg", prefix="emu_")
    with os.fdopen(fd, "w") as f:
        for k, v in base.items():
            f.write(f"{k} = {v}\n")
    return path


def send_cmd(cmd, port=55355):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(cmd.encode(), ("127.0.0.1", port))
        sock.close()
    except OSError:
        pass


def query_status(port=55355, timeout=0.15):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(b"GET_STATUS", ("127.0.0.1", port))
        data, _ = sock.recvfrom(1024)
        sock.close()
        return data.decode(errors="replace").strip()
    except (OSError, socket.timeout):
        return None


_last_played_cache = None


def save_last_played(system_key, rom):
    global _last_played_cache
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    data = {"system": system_key, "rom": str(rom), "name": rom.stem}
    with open(SAVES_DIR / ".last_played.json", "w") as f:
        json.dump(data, f)
    _last_played_cache = data
    _save_state_cache.clear()


def load_last_played():
    global _last_played_cache
    if _last_played_cache is not None:
        rom = Path(_last_played_cache["rom"])
        if rom.exists() and has_save_state(_last_played_cache["system"], rom):
            return _last_played_cache
        return None
    try:
        with open(SAVES_DIR / ".last_played.json") as f:
            data = json.load(f)
        rom = Path(data["rom"])
        if rom.exists() and has_save_state(data["system"], rom):
            _last_played_cache = data
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


def start_game(rom, system_key, system_info, channel=None):
    # skip if this exact game is already running
    if (_active["proc"] and _active["proc"].poll() is None
            and _active["rom"] == rom):
        return

    config = get_config()
    core_name = system_info["core"]
    local_core = ROOT / config["cores_dir"] / f"{core_name}.so"
    core_path = str(local_core) if local_core.exists() else core_name
    retroarch = config["retroarch_path"]

    stop_current_game()

    cfg = write_retroarch_config(system_key, channel=channel)
    proc = subprocess.Popen(
        [retroarch, "-L", core_path, str(rom), "--config", cfg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    _active["proc"] = proc
    _active["system"] = system_key
    _active["rom"] = rom
    _active["cfg"] = cfg
    save_last_played(system_key, rom)


# ── channel overlays ───────────────────────────────────────────────

OVERLAY_DIR = ROOT / "overlays"


def _find_vcr_font():
    candidates = [
        ROOT / "VCR_OSD_MONO_1.001.ttf",
        Path.home() / "Library/Fonts/VCR_OSD_MONO_1.001.ttf",
        Path("/Library/Fonts/VCR_OSD_MONO_1.001.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def generate_overlays(channel_map):
    channels = set(channel_map.values())
    if not channels:
        return

    OVERLAY_DIR.mkdir(exist_ok=True)

    # always rewrite cfg files — two pages per channel:
    #   page 0 = channel number image (visible on launch)
    #   page 1 = blank transparent image (OVERLAY_NEXT switches to this)
    for channel in channels:
        cfg = OVERLAY_DIR / f"ch_{channel:02d}.cfg"
        cfg.write_text(
            f"overlays = 2\n"
            f"overlay0_overlay = ch_{channel:02d}.png\n"
            f"overlay0_full_screen = true\n"
            f"overlay0_descs = 0\n"
            f"overlay1_overlay = blank.png\n"
            f"overlay1_full_screen = true\n"
            f"overlay1_descs = 0\n"
        )

    # check if all PNGs exist (including blank); skip PIL if so
    blank_png = OVERLAY_DIR / "blank.png"
    if blank_png.exists() and all(
        (OVERLAY_DIR / f"ch_{ch:02d}.png").exists() for ch in channels
    ):
        return

    from PIL import Image, ImageDraw, ImageFont

    # generate blank transparent image
    if not blank_png.exists():
        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(blank_png)

    font_path = _find_vcr_font()
    font_size = get_settings().get("overlay_font_size", 120)
    font = (ImageFont.truetype(font_path, font_size) if font_path
            else ImageFont.load_default())
    green = (100, 220, 60, 255)

    for channel in channels:
        png = OVERLAY_DIR / f"ch_{channel:02d}.png"
        if png.exists():
            continue
        img = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        text = f"{channel:02d}"
        draw.text((60, 40), text, font=font, fill=green)
        img.save(png)


def build_channel_map(systems):
    channel = 1
    mapping = {}
    for key, info in systems.items():
        for rom in scan_games(key, info):
            mapping[str(rom)] = channel
            channel += 1
    return mapping


# ── game scanning ───────────────────────────────────────────────────


_scan_cache = {}
_SCAN_TTL = 5  # seconds


def scan_games(system_key, system_info):
    now = time.monotonic()
    cached = _scan_cache.get(system_key)
    if cached and (now - cached[0]) < _SCAN_TTL:
        return cached[1]
    config = get_config()
    roms_dir = ROOT / config["roms_dir"] / system_key
    if not roms_dir.exists():
        result = []
    else:
        exts = set(system_info["extensions"])
        result = sorted(
            [f for f in roms_dir.iterdir() if f.suffix.lower() in exts],
            key=lambda f: f.stem.lower(),
        )
    _scan_cache[system_key] = (now, result)
    return result


# ── settings helpers ────────────────────────────────────────────────

FONT_SIZE_OPTIONS = [
    (60, "Small"),
    (80, "Medium"),
    (120, "Default"),
    (160, "Large"),
    (200, "Extra Large"),
]


def _run_bluetoothctl(stdscr):
    """Suspend curses, run bluetoothctl interactively, then restore."""
    curses.endwin()
    try:
        subprocess.run(["bluetoothctl"])
    except (OSError, FileNotFoundError):
        pass
    stdscr.refresh()
    curses.doupdate()


def _clear_overlay_pngs():
    for png in OVERLAY_DIR.glob("ch_*.png"):
        png.unlink()


# ── control mapping ────────────────────────────────────────────────

SYSTEM_BUTTONS = {
    "nes":     ["up", "down", "left", "right", "a", "b", "start", "select"],
    "snes":    ["up", "down", "left", "right", "a", "b", "x", "y", "l", "r", "start", "select"],
    "gb":      ["up", "down", "left", "right", "a", "b", "start", "select"],
    "gbc":     ["up", "down", "left", "right", "a", "b", "start", "select"],
    "gba":     ["up", "down", "left", "right", "a", "b", "l", "r", "start", "select"],
    "n64":     ["up", "down", "left", "right", "a", "b", "start", "l", "r", "l2"],
    "nds":     ["up", "down", "left", "right", "a", "b", "x", "y", "l", "r", "start", "select"],
    "genesis": ["up", "down", "left", "right", "a", "b", "x", "y", "start"],
    "psx":     ["up", "down", "left", "right", "a", "b", "x", "y", "l", "r", "l2", "r2", "start", "select"],
    "psp":     ["up", "down", "left", "right", "a", "b", "x", "y", "l", "r", "start", "select"],
    "arcade":  ["up", "down", "left", "right", "a", "b", "x", "y", "start", "select"],
}

BUTTON_LABELS = {
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "l": "L", "r": "R", "l2": "L2", "r2": "R2",
    "start": "Start", "select": "Select",
}

DEFAULT_KEYBOARD = {
    "up": "up", "down": "down", "left": "left", "right": "right",
    "a": "x", "b": "z", "x": "s", "y": "a",
    "l": "q", "r": "w", "l2": "e", "r2": "r",
    "start": "enter", "select": "rshift",
}

DEFAULT_GAMEPAD = {
    "up": "h0up", "down": "h0down", "left": "h0left", "right": "h0right",
    "a": "1", "b": "0", "x": "3", "y": "2",
    "l": "4", "r": "5", "l2": "6", "r2": "7",
    "start": "9", "select": "8",
}

GAMEPAD_BUTTON_CODES = {
    "dpad_up": "h0up",
    "dpad_down": "h0down",
    "dpad_left": "h0left",
    "dpad_right": "h0right",
    "buttonA": "0",
    "buttonB": "1",
    "buttonX": "2",
    "buttonY": "3",
    "leftShoulder": "4",
    "rightShoulder": "5",
    "leftTrigger": "6",
    "rightTrigger": "7",
    "buttonOptions": "8",
    "buttonMenu": "9",
}

# display names for RetroArch key values
RA_KEY_DISPLAY = {
    "up": "\u2191", "down": "\u2193", "left": "\u2190", "right": "\u2192",
    "enter": "Enter", "rshift": "R-Shift", "lshift": "L-Shift",
    "space": "Space", "backspace": "Bksp", "tab": "Tab", "escape": "Esc",
    "rctrl": "R-Ctrl", "lctrl": "L-Ctrl", "ralt": "R-Alt", "lalt": "L-Alt",
    "h0up": "Hat \u2191", "h0down": "Hat \u2193", "h0left": "Hat \u2190", "h0right": "Hat \u2192",
}


def _curses_to_ra_key(keycode):
    mapping = {
        curses.KEY_UP: "up", curses.KEY_DOWN: "down",
        curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
        10: "enter", 13: "enter",
        ord(" "): "space",
        curses.KEY_BACKSPACE: "backspace", 127: "backspace",
        ord("\t"): "tab",
    }
    if keycode in mapping:
        return mapping[keycode]
    if 32 < keycode < 127:
        return chr(keycode)
    return None


def _detect_controllers():
    devices = ["Keyboard"]
    seen = set(devices)

    try:
        result = subprocess.run(
            ["bluetoothctl", "--", "devices", "Connected"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            # lines are: "Device AA:BB:CC:DD:EE:FF DeviceName"
            parts = line.strip().split(None, 2)
            if len(parts) == 3 and parts[0] == "Device":
                name = parts[2]
                if name not in seen:
                    devices.append(name)
                    seen.add(name)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return devices


def _read_gamepad_state(device_name):
    controller = _controller_for_device(device_name)
    if controller is None:
        return {}

    _pump_controller_events()
    profile = _controller_profile(controller)
    if profile is None:
        return {}

    state = {}

    dpad = getattr(profile, "dpad", lambda: None)()
    if dpad is not None:
        state["h0up"] = bool(dpad.up().isPressed())
        state["h0down"] = bool(dpad.down().isPressed())
        state["h0left"] = bool(dpad.left().isPressed())
        state["h0right"] = bool(dpad.right().isPressed())

    for attr, code in GAMEPAD_BUTTON_CODES.items():
        if attr.startswith("dpad_"):
            continue
        getter = getattr(profile, attr, None)
        if getter is None:
            continue
        element = getter()
        if element is not None:
            state[code] = bool(element.isPressed())

    return state


def _capture_gamepad_binding(device_name, baseline):
    current = _read_gamepad_state(device_name)
    for code, pressed in current.items():
        if pressed and not baseline.get(code, False):
            return code, current
    return None, current


def _get_mapping(system_key, device):
    settings = get_settings()
    mappings = settings.get("input_mappings", {})
    sys_map = mappings.get(system_key, {})
    device_type = "keyboard" if device == "Keyboard" else "gamepad"
    defaults = DEFAULT_KEYBOARD if device_type == "keyboard" else DEFAULT_GAMEPAD
    buttons = SYSTEM_BUTTONS.get(system_key, SYSTEM_BUTTONS["snes"])
    saved = sys_map.get(device_type, {})
    result = {}
    for btn in buttons:
        result[btn] = saved.get(btn, defaults.get(btn, "nul"))
    return result


def _save_mapping(system_key, device, mapping):
    settings = get_settings()
    if "input_mappings" not in settings:
        settings["input_mappings"] = {}
    if system_key not in settings["input_mappings"]:
        settings["input_mappings"][system_key] = {}
    device_type = "keyboard" if device == "Keyboard" else "gamepad"
    settings["input_mappings"][system_key]["device"] = device_type
    settings["input_mappings"][system_key][device_type] = dict(mapping)
    save_settings(settings)


def _display_key(ra_key):
    if ra_key in RA_KEY_DISPLAY:
        return RA_KEY_DISPLAY[ra_key]
    return ra_key.upper() if len(ra_key) == 1 else ra_key.title()


# ── TUI ─────────────────────────────────────────────────────────────


def _run_with_animation(stdscr, label, work_fn, min_seconds=1.15):
    done = threading.Event()

    def _worker():
        work_fn()
        done.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    spinner = "\u28ff\u28fe\u28fd\u28fb\u28f7\u28ef\u28df\u28bf"
    cols = 12
    rows = 6
    stdscr.timeout(80)
    frame = 0
    start = time.time()
    while not done.is_set() or (time.time() - start) < min_seconds:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        top = h // 2 - rows // 2 - 1
        stdscr.attron(curses.A_BOLD)
        for r in range(rows):
            line = ""
            for c in range(cols):
                line += spinner[(frame + r + c) % len(spinner)]
            stdscr.addnstr(top + r, max(0, (w - cols) // 2), line, w - 2)
        stdscr.addnstr(top + rows + 1, max(0, (w - len(label)) // 2), label, w - 2)
        stdscr.attroff(curses.A_BOLD)
        stdscr.refresh()
        frame += 1
        stdscr.getch()
    stdscr.timeout(1000)


def launch_with_loading(stdscr, rom, system_key, system_info, channel=None):
    # mute the currently running game before showing the loading screen
    if _active["proc"] and _active["proc"].poll() is None:
        send_cmd("MUTE")

    # launch RetroArch in a thread so the UI stays responsive
    launched = threading.Event()

    def _launch():
        start_game(rom, system_key, system_info, channel=channel)
        launched.set()

    thread = threading.Thread(target=_launch, daemon=True)
    thread.start()

    spinner = "\u28ff\u28fe\u28fd\u28fb\u28f7\u28ef\u28df\u28bf"
    cols = 12
    rows = 6
    stdscr.timeout(80)
    frame = 0
    label = f"Loading {rom.stem}..."
    max_wait = 10  # seconds — don't spin forever
    start = time.time()

    while True:
        elapsed = time.time() - start

        # once the process is spawned, poll RetroArch for real status
        if launched.is_set():
            status = query_status()
            if status and "PLAYING" in status:
                break
            # also break if the process already died (bad core, missing rom, etc.)
            if _active["proc"] and _active["proc"].poll() is not None:
                break

        if elapsed > max_wait:
            break

        # draw spinner
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        top = h // 2 - rows // 2 - 1
        stdscr.attron(curses.A_BOLD)
        for r in range(rows):
            line = ""
            for c in range(cols):
                line += spinner[(frame + r + c) % len(spinner)]
            stdscr.addnstr(top + r, max(0, (w - cols) // 2), line, w - 2)
        stdscr.addnstr(top + rows + 1, max(0, (w - len(label)) // 2), label, w - 2)
        stdscr.attroff(curses.A_BOLD)
        stdscr.refresh()
        frame += 1
        stdscr.getch()

    # schedule overlay removal for fade mode
    if get_settings().get("overlay_mode", "fade") == "fade":
        def _fade_overlay():
            time.sleep(3)
            send_cmd("OVERLAY_NEXT")
        threading.Thread(target=_fade_overlay, daemon=True).start()

    stdscr.timeout(1000)


def shutdown_with_animation(stdscr):
    if _active["proc"] and _active["proc"].poll() is None:
        send_cmd("MUTE")
    _run_with_animation(
        stdscr,
        "Shutting down...",
        stop_current_game,
    )


def confirm_save_quit(stdscr):
    """Ask the user to confirm save & quit. Returns True if confirmed."""
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    game = _active["rom"].stem if _active["rom"] else "the game"
    msg = f"Save and quit {game}?"
    hint = " [y/a] yes   [n/b] no "

    row = h // 2 - 1
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(row, max(0, (w - len(msg)) // 2), msg, w - 2)
    stdscr.attroff(curses.A_BOLD)
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(row + 2, max(0, (w - len(hint)) // 2), hint, w - 2)
    stdscr.attroff(curses.A_DIM)
    stdscr.refresh()

    stdscr.timeout(-1)
    while True:
        key = stdscr.getch()
        if key in (ord("y"), ord("Y"), ord("a"), ord("A")):
            stdscr.timeout(1000)
            return True
        if key in (ord("n"), ord("N"), ord("b"), ord("B"), 27):
            stdscr.timeout(1000)
            return False


def draw(stdscr, path_parts, items, cursor, empty_msg, now_playing=None,
         footer_hint=None):
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
    if footer_hint:
        hint = footer_hint
    elif path_parts:
        hint = " [\u2191\u2193] navigate  [enter] play  [esc] back  [q] quit "
    else:
        hint = " [\u2191\u2193] navigate  [enter] open  [q] quit "
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(footer_row, 2, hint, w - 4)
    stdscr.attroff(curses.A_DIM)

    stdscr.refresh()


def draw_volume(stdscr, volume_db, now_playing=None):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    crumb = "emu > Settings > Volume"
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(1, 2, crumb, w - 4)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(2, 2, "\u2500" * min(len(crumb), w - 4), w - 4)

    row = 4
    if now_playing:
        stdscr.addnstr(3, 4, f"\u25b6 Now playing: {now_playing}", w - 6, curses.A_DIM)
        row = 5

    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(row, 4, f"Volume: {volume_db:.0f} dB", w - 6)
    stdscr.attroff(curses.A_BOLD)

    # slider bar
    bar_width = min(40, w - 10)
    filled = round((volume_db + 80) / 92 * bar_width)
    filled = max(0, min(bar_width, filled))
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    stdscr.addnstr(row + 2, 4, f"-80 [{bar}] 12", w - 6)

    hint = " [\u2190\u2192] adjust  [pgup/pgdn] \u00b15  [enter] save  [esc] cancel "
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(h - 1, 2, hint, w - 4)
    stdscr.attroff(curses.A_DIM)

    stdscr.refresh()


def draw_font_size(stdscr, options, cursor, current_size, now_playing=None):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    crumb = "emu > Settings > Overlay Font Size"
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(1, 2, crumb, w - 4)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(2, 2, "\u2500" * min(len(crumb), w - 4), w - 4)

    row = 4
    if now_playing:
        stdscr.addnstr(3, 4, f"\u25b6 Now playing: {now_playing}", w - 6, curses.A_DIM)
        row = 5

    for i, (size, label) in enumerate(options):
        marker = "\u25cf" if size == current_size else "\u25cb"
        text = f"{marker}  {size}   {label}"
        if i == cursor:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(row + i, 3, f" {text} ", w - 5)
            stdscr.attroff(curses.A_REVERSE)
        else:
            stdscr.addnstr(row + i, 4, text, w - 6)

    hint = " [\u2191\u2193] select  [enter] apply  [esc] cancel "
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(h - 1, 2, hint, w - 4)
    stdscr.attroff(curses.A_DIM)

    stdscr.refresh()


def _draw_menu_hotkey(stdscr, settings, now_playing=None):
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    crumb = "emu > Settings > Return to Menu"
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(1, 2, crumb, w - 4)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(2, 2, "\u2500" * min(len(crumb), w - 4), w - 4)

    row = 4
    if now_playing:
        stdscr.addnstr(3, 4, f"\u25b6 Now playing: {now_playing}", w - 6, curses.A_DIM)
        row = 5

    cur = settings.get("hotkeys", {}).get("keyboard", "escape")
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(row, 4, f"Return to Menu: {_display_key(cur)}", w - 6)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(row + 2, 4,
        "Press a key to set as the Return to Menu hotkey.", w - 6, curses.A_DIM)

    hint = " [any key] set binding  [esc] cancel "
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(h - 1, 2, hint, w - 4)
    stdscr.attroff(curses.A_DIM)
    stdscr.refresh()


def draw_controls(stdscr, system_name, buttons, mapping, device_name,
                  cursor, capturing, now_playing=None):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    crumb = f"emu > Settings > Controls > {system_name}"
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(1, 2, crumb, w - 4)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(2, 2, "\u2500" * min(len(crumb), w - 4), w - 4)

    row = 4
    if now_playing:
        stdscr.addnstr(3, 4, f"\u25b6 Now playing: {now_playing}", w - 6, curses.A_DIM)
        row = 5

    # device selector
    device_line = f"  Input: \u25c0 {device_name} \u25b6"
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(row, 2, device_line, w - 4)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(row + 1, 2, "\u2500" * min(len(device_line), w - 4), w - 4)

    list_start = row + 2
    # items: index 0 = Automap, then buttons, then separator + menu hotkey
    all_items = ["automap"] + buttons + ["__sep__", "__menu__"]
    visible = h - list_start - 3
    if visible < 1:
        visible = 1

    if cursor < visible:
        offset = 0
    else:
        offset = cursor - visible + 1

    for i, item in enumerate(all_items[offset:offset + visible]):
        r = list_start + i
        if r >= h - 2:
            break
        idx = i + offset

        if item == "__sep__":
            stdscr.addnstr(r, 4, "\u2500" * min(20, w - 6), w - 6, curses.A_DIM)
            continue
        elif item == "automap":
            text = "\u25b8 Automap"
        elif item == "__menu__":
            menu_key = get_settings().get("hotkeys", {}).get("keyboard", "escape")
            if capturing and idx == cursor:
                val = "Press a key..."
            else:
                val = _display_key(menu_key)
            text = f"  {'Return to Menu':<14} {val}"
        else:
            label = BUTTON_LABELS.get(item, item)
            if capturing and idx == cursor:
                val = "Press a key..."
            else:
                val = _display_key(mapping.get(item, "?"))
            text = f"  {label:<14} {val}"

        if idx == cursor:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(r, 3, f" {text} ", w - 5)
            stdscr.attroff(curses.A_REVERSE)
        else:
            stdscr.addnstr(r, 4, text, w - 6)

    if len(all_items) > visible:
        indicator = f" {cursor + 1}/{len(all_items)} "
        stdscr.addnstr(
            row + 1, max(2, w - len(indicator) - 2), indicator, w - 4, curses.A_DIM
        )

    if capturing:
        hint = " Press a key or button to bind (esc to cancel) "
    else:
        hint = " [\u2191\u2193] navigate  [enter] remap  [\u2190\u2192] device  [esc] save & back "
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(h - 1, 2, hint, w - 4)
    stdscr.attroff(curses.A_DIM)

    stdscr.refresh()


def run(stdscr):
    curses.curs_set(0)
    curses.use_default_colors()
    stdscr.timeout(1000)

    systems = get_systems()
    channel_map = build_channel_map(systems)
    generate_overlays(channel_map)

    level = "systems"
    current_system = None
    current_setting = None
    temp_value = None
    cursor = 0
    items = []
    rebuild_items = True
    refresh_tick = 0
    # control mapping state
    controls_devices = []
    controls_device_idx = 0
    controls_mapping = {}
    controls_capturing = False
    controls_system_key = None
    controls_capture_device = None
    controls_capture_state = {}

    while True:
        _reap_if_exited()

        now_playing = _active["rom"].stem if _active["rom"] else None

        # ── render current level ──

        if level == "controls_buttons":
            sys_info = systems.get(controls_system_key, {})
            sys_name = sys_info.get("name", controls_system_key)
            buttons = SYSTEM_BUTTONS.get(controls_system_key, SYSTEM_BUTTONS["snes"])
            device_name = controls_devices[controls_device_idx]
            draw_controls(
                stdscr, sys_name, buttons, controls_mapping,
                device_name, cursor, controls_capturing, now_playing,
            )

        elif level == "controls_system":
            if rebuild_items:
                items = []
                for key, info in systems.items():
                    if scan_games(key, info):
                        items.append((info["name"], key))
                rebuild_items = False
            draw(
                stdscr, ["Settings", "Controls"], items, cursor,
                "No systems with ROMs found.", now_playing,
                footer_hint=" [\u2191\u2193] navigate  [enter] select  [esc] back  [q] quit ",
            )

        elif level == "setting_detail":
            if current_setting == "volume":
                draw_volume(stdscr, temp_value, now_playing)
            elif current_setting == "menu_hotkey":
                _draw_menu_hotkey(stdscr, get_settings(), now_playing)
            elif current_setting == "font_size":
                draw_font_size(
                    stdscr, FONT_SIZE_OPTIONS, cursor,
                    get_settings()["overlay_font_size"], now_playing,
                )

        elif level == "settings":
            if rebuild_items:
                settings = get_settings()
                overlay_mode = settings.get("overlay_mode", "fade")
                hotkeys = settings.get("hotkeys", {})
                menu_key = hotkeys.get("keyboard", "escape")
                items = [
                    (f"Volume  ({settings['audio_volume']:.0f} dB)", "volume"),
                    (f"Overlay Font Size  ({settings['overlay_font_size']})", "font_size"),
                    (f"Channel Indicator  ({overlay_mode})", "overlay_mode"),
                    (f"Return to Menu  ({_display_key(menu_key)})", "menu_hotkey"),
                    ("Control Mapping", "control_mapping"),
                    ("Bluetooth", "bluetooth"),
                ]
                rebuild_items = False
            draw(
                stdscr, ["Settings"], items, cursor,
                "No settings available.", now_playing,
                footer_hint=" [\u2191\u2193] navigate  [enter] select  [esc] back  [q] quit ",
            )

        elif level == "systems":
            if rebuild_items:
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
                items.append(("\u2699 Settings", ("settings", None)))
                rebuild_items = False
            draw(
                stdscr, [], items, cursor,
                "No ROMs found. Add games to roms/<system>/",
                now_playing,
            )

        elif level == "games":
            if rebuild_items:
                info = systems[current_system]
                games = scan_games(current_system, info)
                items = []
                for g in games:
                    prefix = "\u25cf " if has_save_state(current_system, g) else "  "
                    items.append((f"{prefix}{g.stem}", g))
                rebuild_items = False
            draw(
                stdscr, [current_system], items, cursor,
                "No games in this folder.",
                now_playing,
            )

        # ── input handling ──

        key = stdscr.getch()

        if key == -1:
            if level == "controls_buttons" and controls_capturing and controls_capture_device is not None:
                buttons = SYSTEM_BUTTONS.get(controls_system_key, SYSTEM_BUTTONS["snes"])
                all_items = ["automap"] + buttons + ["__sep__", "__menu__"]
                ra_key, controls_capture_state = _capture_gamepad_binding(
                    controls_capture_device, controls_capture_state
                )
                if ra_key:
                    btn = all_items[cursor]
                    if btn == "__menu__":
                        s = get_settings()
                        s.setdefault("hotkeys", {})["gamepad"] = ra_key
                        save_settings(s)
                    else:
                        controls_mapping[btn] = ra_key
                    controls_capturing = False
                    controls_capture_device = None
                    controls_capture_state = {}
                    stdscr.timeout(1000)
                continue
            refresh_tick += 1
            if refresh_tick >= 5:
                rebuild_items = True
                refresh_tick = 0
            continue

        if level != "controls_buttons" and (key == ord("q") or key == ord("Q")):
            shutdown_with_animation(stdscr)
            break

        # ── setting_detail: volume slider ──

        if level == "setting_detail" and current_setting == "volume":
            if key == curses.KEY_LEFT or key == ord("h"):
                temp_value = max(-80, temp_value - 1)
            elif key == curses.KEY_RIGHT or key == ord("l"):
                temp_value = min(12, temp_value + 1)
            elif key == curses.KEY_PPAGE:
                temp_value = max(-80, temp_value - 5)
            elif key == curses.KEY_NPAGE:
                temp_value = min(12, temp_value + 5)
            elif key in (curses.KEY_ENTER, 10, 13):
                s = get_settings()
                s["audio_volume"] = temp_value
                save_settings(s)
                level = "settings"
                cursor = 0
                rebuild_items = True
            elif key == 27 or key == curses.KEY_BACKSPACE:
                level = "settings"
                cursor = 0
                rebuild_items = True
            continue

        # ── setting_detail: font size picker ──

        if level == "setting_detail" and current_setting == "font_size":
            if key == curses.KEY_UP or key == ord("k"):
                cursor = (cursor - 1) % len(FONT_SIZE_OPTIONS)
            elif key == curses.KEY_DOWN or key == ord("j"):
                cursor = (cursor + 1) % len(FONT_SIZE_OPTIONS)
            elif key in (curses.KEY_ENTER, 10, 13):
                new_size = FONT_SIZE_OPTIONS[cursor][0]
                s = get_settings()
                if s["overlay_font_size"] != new_size:
                    s["overlay_font_size"] = new_size
                    save_settings(s)
                    _clear_overlay_pngs()
                    generate_overlays(channel_map)
                level = "settings"
                cursor = 1
                rebuild_items = True
            elif key == 27 or key == curses.KEY_BACKSPACE:
                level = "settings"
                cursor = 1
                rebuild_items = True
            continue

        # ── setting_detail: menu hotkey ──

        if level == "setting_detail" and current_setting == "menu_hotkey":
            s = get_settings()
            device_type = s.get("input_mappings", {}).get(
                list(s.get("input_mappings", {}).keys())[0], {}
            ).get("device", "keyboard") if s.get("input_mappings") else "keyboard"
            if key == 27:
                level = "settings"
                cursor = 4
                rebuild_items = True
            else:
                ra_key = _curses_to_ra_key(key)
                if ra_key:
                    s.setdefault("hotkeys", {})["keyboard"] = ra_key
                    save_settings(s)
                    level = "settings"
                    cursor = 4
                    rebuild_items = True
            continue

        # ── controls_buttons ──

        if level == "controls_buttons":
            buttons = SYSTEM_BUTTONS.get(controls_system_key, SYSTEM_BUTTONS["snes"])
            all_items = ["automap"] + buttons + ["__sep__", "__menu__"]

            if controls_capturing:
                # capture mode: keyboard keys or controller buttons bind the selected button
                if key == 27:
                    controls_capturing = False
                    controls_capture_device = None
                    controls_capture_state = {}
                    stdscr.timeout(1000)
                elif controls_capture_device is not None:
                    ra_key, controls_capture_state = _capture_gamepad_binding(
                        controls_capture_device, controls_capture_state
                    )
                    if ra_key:
                        btn = all_items[cursor]
                        if btn == "__menu__":
                            s = get_settings()
                            s.setdefault("hotkeys", {})["gamepad"] = ra_key
                            save_settings(s)
                        else:
                            controls_mapping[btn] = ra_key
                        controls_capturing = False
                        controls_capture_device = None
                        controls_capture_state = {}
                        stdscr.timeout(1000)
                else:
                    ra_key = _curses_to_ra_key(key)
                    if ra_key:
                        btn = all_items[cursor]
                        if btn == "__menu__":
                            s = get_settings()
                            s.setdefault("hotkeys", {})["keyboard"] = ra_key
                            save_settings(s)
                        else:
                            controls_mapping[btn] = ra_key
                    controls_capturing = False
                    controls_capture_device = None
                    controls_capture_state = {}
                    stdscr.timeout(1000)
                continue

            def _skip_sep(idx, direction):
                """Advance past __sep__ entries."""
                idx = (idx + direction) % len(all_items)
                if all_items[idx] == "__sep__":
                    idx = (idx + direction) % len(all_items)
                return idx

            if key == curses.KEY_UP or key == ord("k"):
                cursor = _skip_sep(cursor, -1)
            elif key == curses.KEY_DOWN or key == ord("j"):
                cursor = _skip_sep(cursor, 1)
            elif key == curses.KEY_LEFT or key == ord("h"):
                # cycle device left
                controls_device_idx = (controls_device_idx - 1) % len(controls_devices)
                device = controls_devices[controls_device_idx]
                controls_mapping = _get_mapping(controls_system_key, device)
            elif key == curses.KEY_RIGHT or key == ord("l"):
                # cycle device right
                controls_device_idx = (controls_device_idx + 1) % len(controls_devices)
                device = controls_devices[controls_device_idx]
                controls_mapping = _get_mapping(controls_system_key, device)
            elif key in (curses.KEY_ENTER, 10, 13):
                if cursor == 0:
                    # automap
                    device = controls_devices[controls_device_idx]
                    defaults = DEFAULT_KEYBOARD if device == "Keyboard" else DEFAULT_GAMEPAD
                    for btn in buttons:
                        controls_mapping[btn] = defaults.get(btn, "nul")
                else:
                    # enter capture mode for this button
                    controls_capturing = True
                    device = controls_devices[controls_device_idx]
                    if device == "Keyboard":
                        controls_capture_device = None
                        controls_capture_state = {}
                        stdscr.timeout(-1)
                    else:
                        controls_capture_device = device
                        controls_capture_state = _read_gamepad_state(device)
                        stdscr.timeout(50)
            elif key == 27 or key == curses.KEY_BACKSPACE:
                # save and go back
                device = controls_devices[controls_device_idx]
                _save_mapping(controls_system_key, device, controls_mapping)
                level = "controls_system"
                cursor = 0
                rebuild_items = True
                controls_capture_device = None
                controls_capture_state = {}
            continue

        # ── shared navigation for list levels ──

        if key == curses.KEY_UP or key == ord("k"):
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
                    ch = channel_map.get(str(rom))
                    launch_with_loading(stdscr, rom, sys_key, systems[sys_key], channel=ch)
                    rebuild_items = True
                elif isinstance(selected, tuple) and selected[0] == "settings":
                    if _active["proc"] and _active["proc"].poll() is None:
                        if confirm_save_quit(stdscr):
                            shutdown_with_animation(stdscr)
                        else:
                            continue
                    level = "settings"
                    cursor = 0
                    rebuild_items = True
                else:
                    current_system = selected
                    level = "games"
                    cursor = 0
                    rebuild_items = True

            elif level == "games":
                rom = selected
                ch = channel_map.get(str(rom))
                launch_with_loading(stdscr, rom, current_system, systems[current_system], channel=ch)
                rebuild_items = True

            elif level == "settings":
                if selected == "volume":
                    level = "setting_detail"
                    current_setting = "volume"
                    temp_value = get_settings()["audio_volume"]
                elif selected == "font_size":
                    level = "setting_detail"
                    current_setting = "font_size"
                    cur_size = get_settings()["overlay_font_size"]
                    cursor = next(
                        (i for i, (s, _) in enumerate(FONT_SIZE_OPTIONS) if s == cur_size),
                        2,
                    )
                elif selected == "overlay_mode":
                    s = get_settings()
                    modes = ["on", "fade", "off"]
                    cur = s.get("overlay_mode", "fade")
                    s["overlay_mode"] = modes[(modes.index(cur) + 1) % len(modes)]
                    save_settings(s)
                    rebuild_items = True
                elif selected == "fullscreen":
                    s = get_settings()
                    s["fullscreen"] = not s.get("fullscreen", False)
                    save_settings(s)
                    rebuild_items = True
                elif selected == "menu_hotkey":
                    level = "setting_detail"
                    current_setting = "menu_hotkey"
                elif selected == "control_mapping":
                    level = "controls_system"
                    cursor = 0
                    rebuild_items = True
                elif selected == "bluetooth":
                    _run_bluetoothctl(stdscr)

            elif level == "controls_system":
                controls_system_key = selected
                controls_devices = _detect_controllers()
                # restore saved device preference
                saved = get_settings().get("input_mappings", {}).get(selected, {})
                saved_type = saved.get("device", "keyboard")
                if saved_type == "keyboard":
                    controls_device_idx = 0
                else:
                    controls_device_idx = min(1, len(controls_devices) - 1)
                device = controls_devices[controls_device_idx]
                controls_mapping = _get_mapping(selected, device)
                level = "controls_buttons"
                cursor = 0

        elif key == 27 or key == curses.KEY_BACKSPACE or key == curses.KEY_LEFT:
            if level == "games":
                level = "systems"
                current_system = None
                cursor = 0
                rebuild_items = True
            elif level == "controls_system":
                level = "settings"
                cursor = 2  # back to Control Mapping item
                rebuild_items = True
            elif level == "settings":
                level = "systems"
                cursor = 0
                rebuild_items = True
            else:
                shutdown_with_animation(stdscr)
                break


def main():
    curses.wrapper(run)
