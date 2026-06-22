"""Tests for config/systems loading against the real config files."""
from src import cli


def test_get_systems_has_expected_keys():
    systems = cli.get_systems()
    for key in ("nes", "snes", "gba", "n64", "psx"):
        assert key in systems


def test_each_system_has_required_fields():
    for key, info in cli.get_systems().items():
        assert "name" in info, f"{key} missing name"
        assert "core" in info, f"{key} missing core"
        assert isinstance(info["extensions"], list) and info["extensions"], (
            f"{key} has no extensions"
        )


def test_get_config_has_paths():
    config = cli.get_config()
    for key in ("retroarch_path", "roms_dir", "cores_dir"):
        assert key in config


def test_caches_return_same_object():
    first = cli.get_systems()
    second = cli.get_systems()
    assert first is second  # memoised, not reloaded
