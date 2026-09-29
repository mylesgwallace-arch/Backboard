"""Shared test configuration.

Tests marked ``requires_data`` read the local database or processed feature
files under ``data/`` (about 2 GB, not in git). They are skipped when that
data is absent -- e.g. in CI -- so the rest of the suite still runs there.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = [
    ROOT / "data" / "database" / "nba.db",
    ROOT / "data" / "processed" / "game_features.csv",
]
DATA_AVAILABLE = all(path.exists() for path in DATA_FILES)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requires_data: needs the local data/ directory (nba.db, processed features)"
    )


def pytest_collection_modifyitems(config, items):
    if DATA_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="local data/ not available (nba.db / game_features.csv)")
    for item in items:
        if "requires_data" in item.keywords:
            item.add_marker(skip)
