"""Filesystem locations, derived from the repository root.

Nothing in this repo may hardcode a machine-specific absolute path. The
database and the tennis-data.co.uk odds files live under <repo>/data,
which is gitignored (the DB is rebuilt by src/load_data.py; the odds
files are downloaded, not redistributed).

Override the database location with the TENNIS_ORACLE_DB environment
variable, and the odds directory with TENNIS_ORACLE_ODDS.
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DB = os.environ.get("TENNIS_ORACLE_DB") or os.path.join(DATA_DIR, "tennis.db")
ODDS_DIR = os.environ.get("TENNIS_ORACLE_ODDS") or os.path.join(DATA_DIR, "odds")

# tennis-data.co.uk publishes one workbook per season per tour. We store
# them as {year}.xlsx (ATP) and {year}_WTA.xlsx (WTA).
ODDS_YEARS = (2024, 2025, 2026)


def ensure_dirs() -> None:
    """Create the data directories if they are missing (safe to repeat)."""
    os.makedirs(os.path.dirname(DB) or DATA_DIR, exist_ok=True)
    os.makedirs(ODDS_DIR, exist_ok=True)


def odds_path(year: int, tour: str = "ATP") -> str:
    name = f"{year}.xlsx" if tour.upper() == "ATP" else f"{year}_WTA.xlsx"
    return os.path.join(ODDS_DIR, name)


def odds_files(tour: str, years=ODDS_YEARS, existing_only: bool = True) -> list[str]:
    """Paths of the local odds workbooks for one tour."""
    paths = [odds_path(y, tour) for y in years]
    return [p for p in paths if os.path.exists(p)] if existing_only else paths
