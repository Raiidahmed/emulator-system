"""Tests for cli._merge_defaults — the recursive settings-merge helper."""
from src.cli import _merge_defaults


def test_fills_missing_top_level_keys():
    data = {"audio_volume": 50}
    defaults = {"audio_volume": 0, "fullscreen": True}
    result = _merge_defaults(data, defaults)
    assert result["audio_volume"] == 50  # existing value preserved
    assert result["fullscreen"] is True  # missing key filled


def test_recurses_into_nested_dicts():
    data = {"hotkeys": {"keyboard": "escape"}}
    defaults = {"hotkeys": {"keyboard": "nul", "gamepad": "nul"}}
    result = _merge_defaults(data, defaults)
    assert result["hotkeys"]["keyboard"] == "escape"  # preserved
    assert result["hotkeys"]["gamepad"] == "nul"  # filled


def test_non_dict_data_returns_copy_of_defaults():
    defaults = {"a": 1, "b": {"c": 2}}
    result = _merge_defaults("not a dict", defaults)
    assert result == defaults
    # must be a deep copy, not the same object
    result["b"]["c"] = 99
    assert defaults["b"]["c"] == 2


def test_non_dict_defaults_returns_data_unchanged():
    assert _merge_defaults({"x": 1}, "not a dict") == {"x": 1}


def test_does_not_overwrite_falsey_existing_values():
    data = {"audio_volume": 0, "fullscreen": False}
    defaults = {"audio_volume": 100, "fullscreen": True}
    result = _merge_defaults(data, defaults)
    assert result["audio_volume"] == 0
    assert result["fullscreen"] is False
