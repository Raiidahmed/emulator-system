"""Tests for the pure search helper functions."""
import copy
import json
import time

import pytest

from src import cli
from src import browser
from src.browser import build_search_index, filter_search


@pytest.fixture(autouse=True)
def reset_browser_caches():
    """Clear browser's module-level caches before (and after) every test."""
    browser._scan_cache.clear()
    browser._save_state_cache.clear()
    browser._last_played_cache = None
    yield
    browser._scan_cache.clear()
    browser._save_state_cache.clear()
    browser._last_played_cache = None


class TestFilterSearch:

    def test_empty_query_returns_all(self):
        index = [
            ("mario", "Mario", "Game", "game", None),
            ("zelda", "Zelda", "Game", "game", None),
            ("volume", "Volume", "Setting", "settings_nav", 0),
        ]
        assert len(filter_search(index, "")) == 3
        assert len(filter_search(index, "  ")) == 3

    def test_case_insensitive_match(self):
        index = [
            ("super mario", "Super Mario", "Game", "game", None),
            ("the legend of zelda", "The Legend of Zelda", "Game", "game", None),
            ("volume", "Volume", "Setting", "settings_nav", 0),
        ]
        assert len(filter_search(index, "mar")) == 1
        assert filter_search(index, "MAR")[0][1] == "Super Mario"

    def test_substring_match(self):
        index = [
            ("super mario", "Super Mario", "Game", "game", None),
            ("mario kart", "Mario Kart", "Game", "game", None),
            ("zelda", "Zelda", "Game", "game", None),
        ]
        assert len(filter_search(index, "mario")) == 2

    def test_no_match_returns_empty(self):
        index = [
            ("mario", "Mario", "Game", "game", None),
            ("volume", "Volume", "Setting", "settings_nav", 0),
        ]
        assert filter_search(index, "nonexistent") == []

    def test_matches_category_tag(self):
        index = [
            ("super mario", "Super Mario", "Game", "game", None),
            ("volume", "Volume", "Setting", "settings_nav", 0),
        ]
        assert len(filter_search(index, "game")) == 1
        assert filter_search(index, "game")[0][1] == "Super Mario"


class TestBuildSearchIndex:

    @pytest.fixture
    def mock_systems(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "ROOT", tmp_path)
        monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr(browser, "ROOT", tmp_path)
        monkeypatch.setattr(browser, "SAVES_DIR", tmp_path / "saves")
        (tmp_path / "config").mkdir()
        (tmp_path / "config").joinpath("config.json").write_text(
            json.dumps({"roms_dir": "roms", "cores_dir": "cores",
                        "retroarch_path": "/fake/retroarch"}))
        (tmp_path / "config").joinpath("systems.json").write_text(json.dumps({
            "nes": {"name": "NES", "extensions": [".nes"], "core": "nestopia"},
            "snes": {"name": "SNES", "extensions": [".sfc"], "core": "snes9x"},
        }))
        roms = tmp_path / "roms" / "nes"
        roms.mkdir(parents=True)
        (roms / "Mario.nes").touch()
        (roms / "Zelda.nes").touch()
        cli._systems_cache = None
        cli._config_cache = None
        browser._scan_cache.clear()
        return cli.get_systems()

    def test_contains_games(self, mock_systems):
        index = build_search_index(mock_systems)
        game_records = [r for r in index if r[3] == "game"]
        names = {r[1] for r in game_records}
        assert "Mario" in names
        assert "Zelda" in names

    def test_game_search_text_includes_system_name(self, mock_systems):
        index = build_search_index(mock_systems)
        mario = next(r for r in index if r[1] == "Mario")
        assert "nes" in mario[0]

    def test_contains_systems_with_roms(self, mock_systems):
        index = build_search_index(mock_systems)
        sys_records = [r for r in index if r[3] == "system"]
        names = {r[1] for r in sys_records}
        assert "NES" in names

    def test_settings_present(self, mock_systems):
        index = build_search_index(mock_systems)
        setting_records = [r for r in index if r[3] == "settings_nav"]
        labels = {r[1] for r in setting_records}
        assert "Volume" in labels
        assert "Fullscreen" in labels
        assert "Return to Menu" in labels

    def test_features_present(self, mock_systems):
        index = build_search_index(mock_systems)
        feature_records = [r for r in index if r[2] == "Feature" and r[3] != "resume"]
        labels = {r[1] for r in feature_records}
        assert "Settings" in labels
        assert "Control Mapping" in labels
        assert "Bluetooth" in labels

    def test_no_resume_without_last_played(self, mock_systems):
        browser._last_played_cache = None
        index = build_search_index(mock_systems)
        resume_records = [r for r in index if r[3] == "resume"]
        assert len(resume_records) == 0

    def test_resume_with_last_played(self, mock_systems, tmp_path, monkeypatch):
        roms = tmp_path / "roms" / "nes"
        monkeypatch.setattr(browser, "SAVES_DIR", tmp_path / "saves")
        monkeypatch.setattr(browser, "has_save_state", lambda *a: True)
        browser._last_played_cache = {
            "system": "nes", "rom": str(roms / "Mario.nes"), "name": "Mario"
        }
        index = build_search_index(mock_systems)
        resume_records = [r for r in index if r[3] == "resume"]
        assert len(resume_records) == 1
        assert "Mario" in resume_records[0][1]
