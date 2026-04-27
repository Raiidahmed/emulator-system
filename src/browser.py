import curses
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

try:
    import Foundation
    import objc
except ImportError:
    Foundation = None
    objc = None

ROOT = Path(__file__).resolve().parent.parent
SAVES_DIR = ROOT / "saves"

from src.cli import get_config, get_systems, get_settings, save_settings


GAMECONTROLLER_AVAILABLE = False
if objc is not None:
    try:
        objc.loadBundle(
            "GameController",
            globals(),
            bundle_path="/System/Library/Frameworks/GameController.framework",
        )
        GAMECONTROLLER_AVAILABLE = True
    except Exception:
        GAMECONTROLLER_AVAILABLE = False


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
    system_dir = SAVES_DIR / system_key
    # RetroArch stores states in a per-core subdir (e.g. saves/snes/Snes9x/)
    result = False
    if system_dir.exists():
        result = next(system_dir.glob(f"*/{rom.stem}.state.auto"), None) is not None
    _save_state_cache[key] = (now, result)
    return result


_base_cfg_cache = None
_base_cfg_path = None
_base_cfg_mtime = None


def _retroarch_base_config():
    """Read the user's RetroArch config; cached until the file changes."""
    global _base_cfg_cache, _base_cfg_path, _base_cfg_mtime
    config = get_config()
    ra_cfg = Path(config["retroarch_path"]).parent.parent / "Resources" / "retroarch.cfg"
    for candidate in [
        Path.home() / "Library/Application Support/RetroArch/config/retroarch.cfg",
        ra_cfg,
    ]:
        if candidate.exists():
            try:
                mtime = candidate.stat().st_mtime
                if (_base_cfg_cache is not None
                        and candidate == _base_cfg_path
                        and mtime == _base_cfg_mtime):
                    return _base_cfg_cache
                lines = {}
                with open(candidate) as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            k, _, v = line.partition("=")
                            lines[k.strip()] = v.strip()
                _base_cfg_cache = lines
                _base_cfg_path = candidate
                _base_cfg_mtime = mtime
                return lines
            except OSError:
                pass
    return {}


def write_retroarch_config(system_key, channel=None):
    save_dir = ensure_saves_dir(system_key)
    settings = get_settings()

    # start from the user's base RetroArch config (cached)
    base = dict(_retroarch_base_config())

    overrides = {
        "savestate_auto_save": '"true"',
        "savestate_auto_load": '"true"',
        "savestate_directory": f'"{save_dir}"',
        "network_cmd_enable": '"true"',
        "network_cmd_port": '"55355"',
        "audio_volume": f'"{settings["audio_volume"]:.1f}"',
        "config_save_on_exit": '"false"',
        "video_fullscreen": '"false"',       # start windowed; go fullscreen after load
        "video_windowed_fullscreen": '"true"',
    }

    # Return to Menu hotkey: quits RetroArch, returning to TUI
    hotkeys = settings.get("hotkeys", {})
    menu_key = hotkeys.get("keyboard", "escape")
    if menu_key and menu_key != "nul":
        overrides["input_exit_emulator"] = f'"{menu_key}"'
    menu_btn = hotkeys.get("gamepad", "nul")
    if menu_btn and menu_btn != "nul":
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
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(cmd.encode(), ("127.0.0.1", port))
    except OSError:
        pass


def query_status(port=55355, timeout=0.15):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(b"GET_STATUS", ("127.0.0.1", port))
            data, _ = sock.recvfrom(1024)
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
        # cached entry is stale — clear it so we re-read from disk next time
        _last_played_cache = None
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


_game_just_exited = False
_refocus_pending = False  # guard against double-refocus
_terminal_app = None


def _capture_focus():
    """Record which app currently has focus. Runs quickly with a short timeout."""
    global _terminal_app
    try:
        result = subprocess.run(
            ["osascript", "-e",
             "tell application \"System Events\" to get name of "
             "first application process whose frontmost is true"],
            capture_output=True, text=True, timeout=0.3,
        )
        name = result.stdout.strip()
        if name:
            _terminal_app = name
    except (OSError, subprocess.TimeoutExpired):
        pass


def _refocus_terminal():
    global _refocus_pending
    if not _terminal_app or _refocus_pending:
        return
    _refocus_pending = True

    def _activate():
        global _refocus_pending
        time.sleep(0.5)
        subprocess.run(
            ["osascript", "-e", f'tell application "{_terminal_app}" to activate'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _refocus_pending = False

    threading.Thread(target=_activate, daemon=True).start()


def _reap_if_exited():
    global _game_just_exited
    if _active["proc"] and _active["proc"].poll() is not None:
        _save_state_cache.clear()
        _cleanup_active()
        _game_just_exited = True
        _refocus_terminal()


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
    _refocus_terminal()


def start_game(rom, system_key, system_info, channel=None):
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

    blank_png = OVERLAY_DIR / "blank.png"
    if blank_png.exists() and all(
        (OVERLAY_DIR / f"ch_{ch:02d}.png").exists() for ch in channels
    ):
        return

    from PIL import Image, ImageDraw, ImageFont

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
        draw.text((60, 40), f"{channel:02d}", font=font, fill=green)
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


def _clear_overlay_pngs():
    for png in OVERLAY_DIR.glob("ch_*.png"):
        try:
            png.unlink()
        except OSError:
            pass


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
    "dpad_up": "h0up", "dpad_down": "h0down",
    "dpad_left": "h0left", "dpad_right": "h0right",
    "buttonA": "0", "buttonB": "1", "buttonX": "2", "buttonY": "3",
    "leftShoulder": "4", "rightShoulder": "5",
    "leftTrigger": "6", "rightTrigger": "7",
    "buttonOptions": "8", "buttonMenu": "9",
}

RA_KEY_DISPLAY = {
    "up": "\u2191", "down": "\u2193", "left": "\u2190", "right": "\u2192",
    "enter": "Enter", "rshift": "R-Shift", "lshift": "L-Shift",
    "space": "Space", "backspace": "Bksp", "tab": "Tab", "escape": "Esc",
    "rctrl": "R-Ctrl", "lctrl": "L-Ctrl", "ralt": "R-Alt", "lalt": "L-Alt",
    "h0up": "Hat \u2191", "h0down": "Hat \u2193",
    "h0left": "Hat \u2190", "h0right": "Hat \u2192",
}

_controllers_cache = None
_controllers_cache_time = 0.0
_CONTROLLERS_TTL = 10.0  # seconds


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
    global _controllers_cache, _controllers_cache_time
    now = time.monotonic()
    if _controllers_cache is not None and (now - _controllers_cache_time) < _CONTROLLERS_TTL:
        return _controllers_cache

    devices = ["Keyboard"]
    seen = set(devices)

    for name in _game_controller_names():
        if name not in seen:
            devices.append(name)
            seen.add(name)

    if len(devices) > 1 or GAMECONTROLLER_AVAILABLE:
        _controllers_cache = devices
        _controllers_cache_time = now
        return devices

    try:
        result = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            capture_output=True, text=True, timeout=3,
        )
        data = json.loads(result.stdout)
        for section in data.get("SPBluetoothDataType", []):
            connected = section.get("device_connected", [])
            for dev_group in connected:
                for name, info in dev_group.items():
                    minor = info.get("device_minorType", "")
                    if any(t in minor.lower() for t in ("gamepad", "controller", "joystick")):
                        if name not in seen:
                            devices.append(name)
                            seen.add(name)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError):
        pass

    _controllers_cache = devices
    _controllers_cache_time = now
    return devices


def _game_controller_names():
    return [_controller_name(c) for c in _connected_gamepads()]


def _connected_gamepads():
    if not GAMECONTROLLER_AVAILABLE:
        return []
    try:
        controllers = list(GCController.controllers())
    except Exception:
        return []
    return [c for c in controllers if _controller_profile(c) is not None]


def _controller_profile(controller):
    return controller.extendedGamepad() or controller.microGamepad()


def _controller_name(controller):
    name = controller.vendorName() or getattr(controller, "productCategory", lambda: None)()
    return str(name or "Controller")


def _pump_controller_events():
    if Foundation is None or not GAMECONTROLLER_AVAILABLE:
        return
    run_loop = Foundation.NSRunLoop.currentRunLoop()
    until = Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.01)
    run_loop.runMode_beforeDate_(Foundation.NSDefaultRunLoopMode, until)


def _controller_for_device(device_name):
    matches = [c for c in _connected_gamepads() if _controller_name(c) == device_name]
    return matches[0] if matches else None


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
        state["h0up"]    = bool(dpad.up().isPressed())
        state["h0down"]  = bool(dpad.down().isPressed())
        state["h0left"]  = bool(dpad.left().isPressed())
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
    return {btn: saved.get(btn, defaults.get(btn, "nul")) for btn in buttons}


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


def _skip_sep(all_items, idx, direction):
    """Advance cursor past __sep__ sentinel entries."""
    idx = (idx + direction) % len(all_items)
    if all_items[idx] == "__sep__":
        idx = (idx + direction) % len(all_items)
    return idx


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
    start = time.monotonic()
    while not done.is_set() or (time.monotonic() - start) < min_seconds:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        top = h // 2 - rows // 2 - 1
        stdscr.attron(curses.A_BOLD)
        for r in range(rows):
            line = "".join(spinner[(frame + r + c) % len(spinner)] for c in range(cols))
            stdscr.addnstr(top + r, max(0, (w - cols) // 2), line, w - 2)
        stdscr.addnstr(top + rows + 1, max(0, (w - len(label)) // 2), label, w - 2)
        stdscr.attroff(curses.A_BOLD)
        stdscr.refresh()
        frame += 1
        stdscr.getch()
    stdscr.timeout(1000)


def launch_with_loading(stdscr, rom, system_key, system_info, channel=None):
    # capture terminal focus before anything else (quick, non-blocking)
    _capture_focus()

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
    max_wait = 10  # seconds
    start = time.monotonic()

    while True:
        elapsed = time.monotonic() - start

        if launched.is_set():
            status = query_status()
            if status and "PLAYING" in status:
                break
            if _active["proc"] and _active["proc"].poll() is not None:
                break

        if elapsed > max_wait:
            break

        stdscr.clear()
        h, w = stdscr.getmaxyx()
        top = h // 2 - rows // 2 - 1
        stdscr.attron(curses.A_BOLD)
        for r in range(rows):
            line = "".join(spinner[(frame + r + c) % len(spinner)] for c in range(cols))
            stdscr.addnstr(top + r, max(0, (w - cols) // 2), line, w - 2)
        stdscr.addnstr(top + rows + 1, max(0, (w - len(label)) // 2), label, w - 2)
        stdscr.attroff(curses.A_BOLD)
        stdscr.refresh()
        frame += 1
        stdscr.getch()

    # game is loaded — go fullscreen if the setting is on (default True)
    if _active["proc"] and _active["proc"].poll() is None:
        if get_settings().get("fullscreen", True):
            send_cmd("FULLSCREEN_TOGGLE")

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
    _run_with_animation(stdscr, "Shutting down...", stop_current_game)


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
        if key == -1:  # interrupted (signal etc.)
            continue
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

    crumb = " > ".join(["emu"] + path_parts)
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(1, 2, crumb, w - 4)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(2, 2, "\u2500" * min(len(crumb), w - 4), w - 4)

    list_start = 4
    if now_playing:
        stdscr.addnstr(3, 4, f"\u25b6 Now playing: {now_playing}", w - 6, curses.A_DIM)
        list_start = 5

    if not items:
        stdscr.addnstr(list_start, 4, empty_msg, w - 6, curses.A_DIM)
    else:
        visible = max(1, h - list_start - 3)
        offset = 0 if cursor < visible else cursor - visible + 1

        for i, (label, _) in enumerate(items[offset:offset + visible]):
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
            stdscr.addnstr(2, max(2, w - len(indicator) - 2), indicator, w - 4, curses.A_DIM)

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
    bar_width = min(40, w - 10)
    filled = max(0, min(bar_width, round((volume_db + 80) / 92 * bar_width)))
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
    stdscr.addnstr(row + 2, 4, "Press a key to set as the Return to Menu hotkey.", w - 6, curses.A_DIM)
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

    device_line = f"  Input: \u25c0 {device_name} \u25b6"
    stdscr.attron(curses.A_BOLD)
    stdscr.addnstr(row, 2, device_line, w - 4)
    stdscr.attroff(curses.A_BOLD)
    stdscr.addnstr(row + 1, 2, "\u2500" * min(len(device_line), w - 4), w - 4)

    list_start = row + 2
    all_items = ["automap"] + buttons + ["__sep__", "__menu__"]
    visible = max(1, h - list_start - 3)
    offset = 0 if cursor < visible else cursor - visible + 1

    # snapshot hotkey so we don't call get_settings() per row
    menu_key = get_settings().get("hotkeys", {}).get("keyboard", "escape")

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
            val = "Press a key..." if (capturing and idx == cursor) else _display_key(menu_key)
            text = f"  {'Return to Menu':<14} {val}"
        else:
            label = BUTTON_LABELS.get(item, item)
            val = "Press a key..." if (capturing and idx == cursor) else _display_key(mapping.get(item, "?"))
            text = f"  {label:<14} {val}"

        if idx == cursor:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(r, 3, f" {text} ", w - 5)
            stdscr.attroff(curses.A_REVERSE)
        else:
            stdscr.addnstr(r, 4, text, w - 6)

    if len(all_items) > visible:
        indicator = f" {cursor + 1}/{len(all_items)} "
        stdscr.addnstr(row + 1, max(2, w - len(indicator) - 2), indicator, w - 4, curses.A_DIM)

    hint = (" Press a key or button to bind (esc to cancel) " if capturing
            else " [\u2191\u2193] navigate  [enter] remap  [\u2190\u2192] device  [esc] save & back ")
    stdscr.attron(curses.A_DIM)
    stdscr.addnstr(h - 1, 2, hint, w - 4)
    stdscr.attroff(curses.A_DIM)
    stdscr.refresh()


def run(stdscr):
    global _game_just_exited
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
    last_idle_rebuild = time.monotonic()
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

        if _game_just_exited:
            _game_just_exited = False
            rebuild_items = True
            level = "systems"
            current_system = None
            cursor = 0

        stdscr.timeout(100 if _active["proc"] else 1000)
        now_playing = _active["rom"].stem if _active["rom"] else None

        # ── render ──

        if level == "controls_buttons":
            sys_info = systems.get(controls_system_key, {})
            sys_name = sys_info.get("name", controls_system_key)
            buttons = SYSTEM_BUTTONS.get(controls_system_key, SYSTEM_BUTTONS["snes"])
            draw_controls(stdscr, sys_name, buttons, controls_mapping,
                          controls_devices[controls_device_idx],
                          cursor, controls_capturing, now_playing)

        elif level == "controls_system":
            if rebuild_items:
                items = [(info["name"], key)
                         for key, info in systems.items() if scan_games(key, info)]
                rebuild_items = False
            draw(stdscr, ["Settings", "Controls"], items, cursor,
                 "No systems with ROMs found.", now_playing,
                 footer_hint=" [\u2191\u2193] navigate  [enter] select  [esc] back  [q] quit ")

        elif level == "setting_detail":
            if current_setting == "volume":
                draw_volume(stdscr, temp_value, now_playing)
            elif current_setting == "menu_hotkey":
                _draw_menu_hotkey(stdscr, get_settings(), now_playing)
            elif current_setting == "font_size":
                draw_font_size(stdscr, FONT_SIZE_OPTIONS, cursor,
                               get_settings()["overlay_font_size"], now_playing)

        elif level == "settings":
            if rebuild_items:
                s = get_settings()
                overlay_mode = s.get("overlay_mode", "fade")
                fullscreen = s.get("fullscreen", True)
                menu_key = s.get("hotkeys", {}).get("keyboard", "escape")
                items = [
                    (f"Volume  ({s['audio_volume']:.0f} dB)", "volume"),
                    (f"Overlay Font Size  ({s['overlay_font_size']})", "font_size"),
                    (f"Channel Indicator  ({overlay_mode})", "overlay_mode"),
                    (f"Fullscreen  ({'on' if fullscreen else 'off'})", "fullscreen"),
                    (f"Return to Menu  ({_display_key(menu_key)})", "menu_hotkey"),
                    ("Control Mapping", "control_mapping"),
                    ("Bluetooth", "bluetooth"),
                ]
                rebuild_items = False
            draw(stdscr, ["Settings"], items, cursor,
                 "No settings available.", now_playing,
                 footer_hint=" [\u2191\u2193] navigate  [enter] select  [esc] back  [q] quit ")

        elif level == "systems":
            if rebuild_items:
                items = []
                last = load_last_played()
                if last and last["system"] in systems:
                    items.append((f"\u25b8 Resume: {last['name']}", ("resume", last)))
                for key, info in systems.items():
                    games = scan_games(key, info)
                    if games:
                        items.append((f"{info['name']}  ({len(games)})", key))
                items.append(("\u2699 Settings", ("settings", None)))
                rebuild_items = False
            draw(stdscr, [], items, cursor,
                 "No ROMs found. Add games to roms/<system>/", now_playing)

        elif level == "games":
            if rebuild_items:
                info = systems[current_system]
                games = scan_games(current_system, info)
                items = []
                for g in games:
                    prefix = "\u25cf " if has_save_state(current_system, g) else "  "
                    items.append((f"{prefix}{g.stem}", g))
                rebuild_items = False
            draw(stdscr, [current_system], items, cursor,
                 "No games in this folder.", now_playing)

        # ── input ──

        key = stdscr.getch()

        if key == -1:
            # gamepad polling for capture mode
            if level == "controls_buttons" and controls_capturing and controls_capture_device is not None:
                buttons = SYSTEM_BUTTONS.get(controls_system_key, SYSTEM_BUTTONS["snes"])
                all_items = ["automap"] + buttons + ["__sep__", "__menu__"]
                ra_key, controls_capture_state = _capture_gamepad_binding(
                    controls_capture_device, controls_capture_state)
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
            # time-based idle rebuild (consistent 5s regardless of poll rate)
            now = time.monotonic()
            if now - last_idle_rebuild >= 5.0:
                rebuild_items = True
                last_idle_rebuild = now
            continue

        if level != "controls_buttons" and (key == ord("q") or key == ord("Q")):
            shutdown_with_animation(stdscr)
            break

        # ── volume slider ──

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

        # ── font size picker ──

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

        # ── menu hotkey capture ──

        if level == "setting_detail" and current_setting == "menu_hotkey":
            if key == 27:
                level = "settings"
                cursor = 4
                rebuild_items = True
            else:
                ra_key = _curses_to_ra_key(key)
                if ra_key:
                    s = get_settings()
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
                if key == 27:
                    controls_capturing = False
                    controls_capture_device = None
                    controls_capture_state = {}
                    stdscr.timeout(1000)
                elif controls_capture_device is not None:
                    ra_key, controls_capture_state = _capture_gamepad_binding(
                        controls_capture_device, controls_capture_state)
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

            if key == curses.KEY_UP or key == ord("k"):
                cursor = _skip_sep(all_items, cursor, -1)
            elif key == curses.KEY_DOWN or key == ord("j"):
                cursor = _skip_sep(all_items, cursor, 1)
            elif key == curses.KEY_LEFT or key == ord("h"):
                controls_device_idx = (controls_device_idx - 1) % len(controls_devices)
                controls_mapping = _get_mapping(controls_system_key,
                                                controls_devices[controls_device_idx])
            elif key == curses.KEY_RIGHT or key == ord("l"):
                controls_device_idx = (controls_device_idx + 1) % len(controls_devices)
                controls_mapping = _get_mapping(controls_system_key,
                                                controls_devices[controls_device_idx])
            elif key in (curses.KEY_ENTER, 10, 13):
                if cursor == 0:
                    device = controls_devices[controls_device_idx]
                    defaults = DEFAULT_KEYBOARD if device == "Keyboard" else DEFAULT_GAMEPAD
                    for btn in buttons:
                        controls_mapping[btn] = defaults.get(btn, "nul")
                else:
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
                device = controls_devices[controls_device_idx]
                _save_mapping(controls_system_key, device, controls_mapping)
                level = "controls_system"
                cursor = 0
                rebuild_items = True
                controls_capture_device = None
                controls_capture_state = {}
            continue

        # ── shared list navigation ──

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
                launch_with_loading(stdscr, rom, current_system,
                                    systems[current_system], channel=ch)
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
                        (i for i, (s, _) in enumerate(FONT_SIZE_OPTIONS) if s == cur_size), 2)
                elif selected == "overlay_mode":
                    s = get_settings()
                    modes = ["on", "fade", "off"]
                    cur = s.get("overlay_mode", "fade")
                    s["overlay_mode"] = modes[(modes.index(cur) + 1) % len(modes)]
                    save_settings(s)
                    rebuild_items = True
                elif selected == "fullscreen":
                    s = get_settings()
                    s["fullscreen"] = not s.get("fullscreen", True)
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
                    subprocess.Popen(
                        ["open", "/System/Library/PreferencePanes/Bluetooth.prefPane"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )

            elif level == "controls_system":
                controls_system_key = selected
                controls_devices = _detect_controllers()
                saved = get_settings().get("input_mappings", {}).get(selected, {})
                saved_type = saved.get("device", "keyboard")
                controls_device_idx = (0 if saved_type == "keyboard"
                                        else min(1, len(controls_devices) - 1))
                controls_mapping = _get_mapping(selected, controls_devices[controls_device_idx])
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
                cursor = 2
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
