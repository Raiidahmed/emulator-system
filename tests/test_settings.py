"""Tests for settings load/save behaviour in cli."""
import copy
import json

import pytest

from src import cli


def test_get_settings_uses_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "SETTINGS_PATH", tmp_path / "does_not_exist.json")
    settings = cli.get_settings()
    assert settings["overlay_font_size"] == 120
    assert settings["hotkeys"]["keyboard"] == "escape"


def test_get_settings_recovers_from_invalid_json(tmp_path, monkeypatch):
    bad = tmp_path / "settings.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(cli, "SETTINGS_PATH", bad)
    settings = cli.get_settings()
    # falls back to defaults rather than raising
    assert settings["aspect_ratio"] == "auto"


def test_get_settings_merges_partial_file_with_defaults(tmp_path, monkeypatch):
    partial = tmp_path / "settings.json"
    partial.write_text(json.dumps({"audio_volume": 75}), encoding="utf-8")
    monkeypatch.setattr(cli, "SETTINGS_PATH", partial)
    settings = cli.get_settings()
    assert settings["audio_volume"] == 75  # from file
    assert settings["fast_forward"] == 2  # filled from defaults
    assert "channel_up_keyboard" in settings["hotkeys"]  # nested default filled


def test_save_settings_round_trips(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "SETTINGS_PATH", target)
    payload = copy.deepcopy(cli.SETTINGS_DEFAULTS)
    payload["audio_volume"] = 42
    cli.save_settings(payload)

    assert target.exists()
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["audio_volume"] == 42
    # temp file must not be left behind
    assert not target.with_suffix(".tmp").exists()


def test_save_then_get_returns_saved_values(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "SETTINGS_PATH", target)
    payload = copy.deepcopy(cli.SETTINGS_DEFAULTS)
    payload["overlay_mode"] = "instant"
    cli.save_settings(payload)
    cli._settings_cache = None  # force a fresh read from disk
    assert cli.get_settings()["overlay_mode"] == "instant"
