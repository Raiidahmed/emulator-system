"""Tests for the user-facing command functions (print output + exit codes)."""
import pytest

from src import cli


def test_list_systems_prints_all_systems(capsys):
    cli.list_systems()
    out = capsys.readouterr().out
    assert "nes" in out
    assert "Super Nintendo" in out
    # header + separator + one line per system
    assert "Core" in out


def test_list_games_unknown_system_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.list_games("not_a_real_system")
    assert exc.value.code == 1
    assert "Unknown system" in capsys.readouterr().out


def test_launch_unknown_system_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.launch("not_a_real_system", "anything")
    assert exc.value.code == 1
    assert "Unknown system" in capsys.readouterr().out


def test_launch_missing_rom_dir_exits(tmp_path, monkeypatch, capsys):
    # point ROOT at an empty dir so no rom directory exists for any system
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.launch("nes", "mario")
    assert exc.value.code == 1
    assert "No ROM directory" in capsys.readouterr().out
