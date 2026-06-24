# Implementation Plan: Favorites

Self-contained spec for implementing a **Favorites** feature in this repo.
Implement exactly as written; do not redesign or expand scope.

## Files you may edit
- `src/browser.py` — the curses TUI + RetroArch process logic.
- `src/cli.py` — the argparse CLI.
- `tests/test_favorites.py` — new test file (create it).

Read `tests/test_settings.py` once to match the existing test style
(tmp_path + monkeypatch, atomic-write round-trips).

## Verify when done
```bash
python3 -m pytest -q
```
All tests (old and new) must pass.

---

## Goal
Let users star/unstar games and quickly return to them. Adds:
1. A persistent favorites store (`saves/.favorites.json`).
2. A ★ marker + `f` toggle key in the game list.
3. A Favorites screen reachable from the main menu (and global search).
4. A read-only `emu favorites` CLI command.

All persistence logic lives in pure, unit-tested helpers.

## Data model
- File: `saves/.favorites.json` (the `saves/` dir is gitignored; `.last_played.json` already lives there).
- Format: JSON list of `{"system": <key>, "name": <rom.stem>}`.
- Store `system + stem` (NOT an absolute path) so entries survive the project
  moving; resolve to a real `Path` at display/launch time by scanning.

---

## Step 1 — Pure helpers in `src/browser.py`

Add near the save-last-played helpers (search for `def load_last_played`,
around line 230). `SAVES_DIR` and `json`/`Path` are already imported.

```python
FAVORITES_PATH = SAVES_DIR / ".favorites.json"
_favorites_cache = None


def _fav_key(system_key, name):
    return (system_key, name)


def _toggle_in_list(favs, system_key, name):
    """Pure: return a new favorites list with (system,name) flipped."""
    out = [f for f in favs
           if (f.get("system"), f.get("name")) != _fav_key(system_key, name)]
    if len(out) == len(favs):          # wasn't present -> add it
        out.append({"system": system_key, "name": name})
    return out


def load_favorites():
    global _favorites_cache
    if _favorites_cache is not None:
        return _favorites_cache
    try:
        with open(FAVORITES_PATH) as f:
            data = json.load(f)
        favs = [e for e in data
                if isinstance(e, dict) and "system" in e and "name" in e]
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        favs = []
    _favorites_cache = favs
    return favs


def save_favorites(favs):
    global _favorites_cache
    _favorites_cache = favs
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FAVORITES_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(favs, f, indent=2)
        tmp.replace(FAVORITES_PATH)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def is_favorite(system_key, rom):
    return any((f.get("system"), f.get("name")) == _fav_key(system_key, rom.stem)
               for f in load_favorites())


def toggle_favorite(system_key, rom):
    save_favorites(_toggle_in_list(load_favorites(), system_key, rom.stem))
    return is_favorite(system_key, rom)


def favorite_games(systems):
    """Resolve favorites to (system_key, info, rom Path); skip missing ROMs."""
    out = []
    for fav in load_favorites():
        key = fav["system"]
        info = systems.get(key)
        if not info:
            continue
        rom = next((g for g in scan_games(key, info) if g.stem == fav["name"]), None)
        if rom is not None:
            out.append((key, info, rom))
    return out
```

---

## Step 2 — ★ marker + `f` toggle in the game list (TUI)

In `run(stdscr)`:

**(a) Render the star.** Find the `elif level == "games":` render block (it
builds `items` with a `prefix` using `has_save_state`). Replace the row build so
each row shows a favorite star plus the existing save-state dot:

```python
            if rebuild_items:
                info = systems[current_system]
                games = scan_games(current_system, info)
                items = []
                for g in games:
                    star = "\u2605" if is_favorite(current_system, g) else " "
                    dot = "\u25cf" if has_save_state(current_system, g) else " "
                    items.append((f"{star}{dot} {g.stem}", g))
                rebuild_items = False
            draw(stdscr, [current_system], items, cursor,
                 "No games in this folder.", now_playing,
                 footer_hint=" [\u2191\u2193] navigate  [enter] play  "
                             "[f] favorite  [esc] back  [q] quit ")
```

**(b) Toggle key.** In the "shared list navigation" section (search for
`# ── shared list navigation ──`), the chain starts with
`if key == curses.KEY_UP ... elif key == curses.KEY_DOWN ... elif key in (curses.KEY_ENTER, ...)`.
Add this branch into that same `if/elif` chain (e.g. right after the
KEY_DOWN branch):

```python
        elif level == "games" and key in (ord("f"), ord("F")):
            if items:
                toggle_favorite(current_system, items[cursor][1])
                rebuild_items = True
```

(`f`/`F` is currently unbound at the games level, so there is no conflict.)

---

## Step 3 — Favorites screen + menu entry (TUI)

**(a) Menu row.** In the `elif level == "systems":` render block, the code
appends rows for Search and Settings. Add a Favorites row just before the
Search row, shown only when favorites exist:

```python
                if favorite_games(systems):
                    items.append(("\u2605 Favorites", ("favorites", None)))
```

**(b) Render the new level.** Add a render branch next to the existing
`elif level == "search":` branch:

```python
        elif level == "favorites":
            if rebuild_items:
                items = [(f"{rom.stem}   [{info['name']}]", (key, rom))
                         for key, info, rom in favorite_games(systems)]
                rebuild_items = False
            draw(stdscr, ["Favorites"], items, cursor, "No favorites yet.",
                 now_playing,
                 footer_hint=" [\u2191\u2193] navigate  [enter] play  "
                             "[esc] back  [q] quit ")
```

**(c) Open it from the systems menu.** In the `if level == "systems":` block of
the Enter handler (where `selected[0] == "resume"` / `"settings"` /
`"search"` are handled), add a branch:

```python
                elif isinstance(selected, tuple) and selected[0] == "favorites":
                    level = "favorites"
                    cursor = 0
                    rebuild_items = True
```

**(d) Launch on Enter.** In the shared Enter handler, after the
`elif level == "games":` launch branch, add:

```python
            elif level == "favorites":
                sys_key, rom = selected
                ch = channel_map.get(str(rom))
                launch_with_loading(stdscr, rom, sys_key, systems[sys_key], channel=ch)
                rebuild_items = True
```

**(e) Esc/back.** In the esc/back block (search for
`key == 27 or key == curses.KEY_BACKSPACE or key == curses.KEY_LEFT`), add:

```python
            elif level == "favorites":
                level = "systems"
                cursor = 0
                rebuild_items = True
```

---

## Step 4 — Surface in global search (OPTIONAL — do last)

In `build_search_index(systems)` (already exists in `src/browser.py`), append a
Feature record so search can jump to the screen:

```python
    if favorite_games(systems):
        index.append(("favorites", "Favorites", "Feature", "favorites_nav", None))
```

In the search Enter handler (the block under `if level == "search":` that
dispatches on `kind`), add:

```python
                    elif kind == "favorites_nav":
                        level = "favorites"; cursor = 0; rebuild_items = True
```

---

## Step 5 — CLI: `emu favorites` (read-only) in `src/cli.py`

In `main()`, where other subparsers are registered, add:

```python
    sub.add_parser("favorites", aliases=["favs"], help="List favorite games")
```

In the dispatch chain (where `args.command` is handled), add:

```python
    elif args.command in {"favorites", "favs"}:
        from src.browser import favorite_games
        systems = get_systems()
        favs = favorite_games(systems)
        if not favs:
            print("No favorites yet. Star games in the browser with [f].")
        else:
            for key, info, rom in favs:
                print(f"  {rom.stem}  ({info['name']})")
```

(No CLI command to add/remove favorites — starring stays in the TUI.)

---

## Step 6 — Tests: create `tests/test_favorites.py`

Mirror `tests/test_settings.py` style. Add an autouse fixture that resets
`browser._favorites_cache` and `browser._scan_cache` before/after each test.
Cover the PURE logic (no curses):

- `_toggle_in_list`: adds when absent, removes when present, returns a NEW list
  (does not mutate the input).
- `load_favorites`: missing file -> `[]`; malformed JSON -> `[]`; drops
  malformed (non-dict / missing-key) entries.
- `save_favorites` -> `load_favorites` round-trips; no `.tmp` file left behind.
  (Point `browser.FAVORITES_PATH` and `browser.SAVES_DIR` at `tmp_path` via
  monkeypatch.)
- `is_favorite` / `toggle_favorite`: flip semantics against a tmp store.
- `favorite_games`: with a tmp roms dir containing one real ROM and one
  favorite whose ROM is missing, returns only the existing one.

Example skeleton:

```python
"""Tests for the favorites helpers."""
import json
import pytest

from src import cli
from src import browser


@pytest.fixture(autouse=True)
def reset_caches():
    browser._favorites_cache = None
    browser._scan_cache.clear()
    yield
    browser._favorites_cache = None
    browser._scan_cache.clear()


def test_toggle_in_list_adds_when_absent():
    out = browser._toggle_in_list([], "nes", "Mario")
    assert {"system": "nes", "name": "Mario"} in out


def test_toggle_in_list_removes_when_present():
    favs = [{"system": "nes", "name": "Mario"}]
    assert browser._toggle_in_list(favs, "nes", "Mario") == []


def test_toggle_in_list_does_not_mutate_input():
    favs = [{"system": "nes", "name": "Mario"}]
    browser._toggle_in_list(favs, "snes", "Zelda")
    assert favs == [{"system": "nes", "name": "Mario"}]


def test_load_favorites_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "FAVORITES_PATH", tmp_path / "nope.json")
    assert browser.load_favorites() == []


def test_load_favorites_bad_json(tmp_path, monkeypatch):
    p = tmp_path / "favs.json"
    p.write_text("{ not json")
    monkeypatch.setattr(browser, "FAVORITES_PATH", p)
    assert browser.load_favorites() == []


def test_load_favorites_drops_malformed_entries(tmp_path, monkeypatch):
    p = tmp_path / "favs.json"
    p.write_text(json.dumps([{"system": "nes", "name": "Mario"}, {"x": 1}, "bad"]))
    monkeypatch.setattr(browser, "FAVORITES_PATH", p)
    assert browser.load_favorites() == [{"system": "nes", "name": "Mario"}]


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(browser, "FAVORITES_PATH", tmp_path / "favs.json")
    payload = [{"system": "snes", "name": "Zelda"}]
    browser.save_favorites(payload)
    browser._favorites_cache = None
    assert browser.load_favorites() == payload
    assert not (tmp_path / "favs.tmp").exists()


def test_favorite_games_skips_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(browser, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.json").write_text(
        json.dumps({"roms_dir": "roms", "cores_dir": "cores",
                    "retroarch_path": "/fake"}))
    (tmp_path / "config" / "systems.json").write_text(json.dumps({
        "nes": {"name": "NES", "extensions": [".nes"], "core": "nestopia"}}))
    roms = tmp_path / "roms" / "nes"
    roms.mkdir(parents=True)
    (roms / "Mario.nes").touch()
    cli._config_cache = None
    cli._systems_cache = None
    monkeypatch.setattr(browser, "FAVORITES_PATH", tmp_path / "favs.json")
    browser._favorites_cache = [
        {"system": "nes", "name": "Mario"},
        {"system": "nes", "name": "Ghost"},   # ROM does not exist
    ]
    systems = cli.get_systems()
    resolved = browser.favorite_games(systems)
    assert len(resolved) == 1
    assert resolved[0][2].stem == "Mario"
```

(If your `conftest.py` resets `cli` caches, keep doing so; the local fixture
above only adds the browser-cache reset.)

---

## Out of scope (do NOT do)
- No CLI command to add/remove favorites (TUI-only mutation).
- No reordering/sorting of favorites (insertion order).
- Do not touch the `emu search` CLI command.

## Acceptance criteria
1. Pressing `f` on a game toggles a ★ marker that persists across restarts.
2. A `★ Favorites` row appears in the main menu only when favorites exist and
   opens a working Favorites screen; Enter launches, Esc returns.
3. (If Step 4 done) global search lists a `Favorites` feature entry.
4. `emu favorites` lists starred games grouped by system.
5. `python3 -m pytest -q` passes, including `tests/test_favorites.py`.
