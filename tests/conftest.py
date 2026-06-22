"""Shared fixtures for the test suite.

The cli module memoises config/systems/settings in module-level globals.
Tests must start from a clean slate, so we reset those caches before each test.
"""
import pytest

from src import cli


@pytest.fixture(autouse=True)
def reset_cli_caches():
    """Clear cli's module-level caches before (and after) every test."""
    cli._config_cache = None
    cli._systems_cache = None
    cli._settings_cache = None
    yield
    cli._config_cache = None
    cli._systems_cache = None
    cli._settings_cache = None
