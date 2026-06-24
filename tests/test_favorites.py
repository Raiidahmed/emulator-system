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
