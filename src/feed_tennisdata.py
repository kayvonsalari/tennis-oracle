"""Import match results and closing odds from tennis-data.co.uk.

Why this exists: the Sackmann forks the ratings are trained on are frozen
(ATP 2026-06-01, WTA 2026-04-27) because the original repos were removed
from GitHub. tennis-data.co.uk publishes both tours weekly, so it keeps
the ratings current going forward. It does NOT replace Sackmann: it has
no Challenger or ITF matches, and those are worth 1.2-1.7 percentage
points of accuracy because they give newcomers a real rating before their
first tour match.

Two jobs:
  1. append matches newer than what the database already holds
  2. attach closing odds to matches, new and historical

Rows whose players cannot be resolved to existing IDs are written to
`feed_unmatched` rather than dropped, so a broken join is visible instead
of silently shrinking the data.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths
from namematch import (
    date_int,
    implied_probability,
    key_variants_td,
    keys_sack,
    normalise_surface,
    within_window,
)

BASE_URL = "http://www.tennis-data.co.uk"
UA = {"User-Agent": "tennis-oracle/0.1 (personal research)"}

ODDS_COLUMNS = [
    ("odds_winner", "REAL"),
    ("odds_loser", "REAL"),
    ("odds_source", "TEXT"),
]

UNMATCHED_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_unmatched (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tour         TEXT,
    match_date   INTEGER,
    tourney_name TEXT,
    winner_raw   TEXT,
    loser_raw    TEXT,
    surface      TEXT,
    reason       TEXT,
    source_file  TEXT,
    UNIQUE(tour, match_date, winner_raw, loser_raw)
);
"""


# --------------------------------------------------------------- schema

def ensure_schema(con: sqlite3.Connection) -> None:
    """Add odds columns and the unmatched table. Safe to run repeatedly."""
    existing = {r[1] for r in con.execute("PRAGMA table_info(matches)")}
    for name, coltype in ODDS_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE matches ADD COLUMN {name} {coltype}")
    con.executescript(UNMATCHED_SCHEMA)
    con.commit()


# ------------------------------------------------------------ download

def season_url(year: int, tour: str) -> str:
    """tennis-data.co.uk layout: /YYYY/YYYY.xlsx (ATP), /YYYYw/YYYY.xlsx (WTA)."""
    return (f"{BASE_URL}/{year}/{year}.xlsx" if tour.upper() == "ATP"
            else f"{BASE_URL}/{year}w/{year}.xlsx")


def download_season(year: int, tour: str, dest_dir: str | None = None) -> str:
    """Fetch one season workbook. Returns the local path."""
    dest_dir = dest_dir or paths.ODDS_DIR
    os.makedirs(dest_dir, exist_ok=True)
    dest = paths.odds_path(year, tour) if dest_dir == paths.ODDS_DIR else \
        os.path.join(dest_dir, os.path.basename(paths.odds_path(year, tour)))
    req = urllib.request.Request(season_url(year, tour), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as fh:
        fh.write(r.read())
    return dest


# --------------------------------------------------------------- index

def build_player_index(con: sqlite3.Connection, tour: str) -> dict:
    """(surname, initial) -> [(player_id, first_date, last_date), ...].

    Every plausible reading of each Sackmann name is indexed, because the
    first/last name boundary is not marked in the source. Collisions are
    kept rather than dropped: two players really can share surname and
    initial (Bryan Shelton, Ben Shelton), and the match date tells them
    apart — see resolve().
    """
    activity: dict[int, list] = {}
    names: dict[int, set] = {}
    q = """SELECT winner_id, winner_name, MIN(tourney_date), MAX(tourney_date),
                  COUNT(*)
           FROM matches WHERE tour=? GROUP BY winner_id, winner_name
           UNION ALL
           SELECT loser_id, loser_name, MIN(tourney_date), MAX(tourney_date),
                  COUNT(*)
           FROM matches WHERE tour=? GROUP BY loser_id, loser_name"""
    for pid, name, lo, hi, n in con.execute(q, (tour, tour)):
        if pid is None or not name:
            continue
        names.setdefault(pid, set()).add(name)
        a = activity.get(pid)
        activity[pid] = ([min(a[0], lo), max(a[1], hi), a[2] + n] if a
                         else [lo, hi, n])

    index: dict[tuple[str, str], list] = {}
    surname_index: dict[str, list] = {}
    for pid, nameset in names.items():
        lo, hi, n = activity[pid]
        for name in nameset:
            for k in keys_sack(name):
                bucket = index.setdefault(k, [])
                if not any(e[0] == pid for e in bucket):
                    bucket.append((pid, lo, hi, n))
                sb = surname_index.setdefault(k[0], [])
                if not any(e[0] == pid for e in sb):
                    sb.append((pid, lo, hi, n))
    index["__surnames__"] = surname_index      # type: ignore[assignment]
    return index


DOMINANCE = 5      # a candidate this many times more active wins outright


def _pick(cands: list, match_date: int) -> int | None:
    """Choose among candidates sharing a key.

    1. Keep those whose career window brackets the match date (1yr slack).
       tennis-data files are tour-level, so a player with a single ITF
       match in 2023 is rarely the "Krueger A." of a 2025 tour draw.
    2. If one survivor is DOMINANCE times more active than every other,
       take it. Otherwise decline: a wrong ID corrupts ratings silently,
       an unmatched row is visible in feed_unmatched.
    """
    live = [c for c in cands if (c[1] - 10000) <= match_date <= (c[2] + 10000)]
    if not live:
        return None
    if len(live) == 1:
        return live[0][0]
    live.sort(key=lambda c: c[3], reverse=True)
    if live[0][3] >= DOMINANCE * live[1][3]:
        return live[0][0]
    return None


def resolve(index: dict, raw_name: str, match_date: int) -> int | None:
    """tennis-data name + date -> player_id, or None if not confidently known."""
    for k in key_variants_td(raw_name):
        cands = index.get(k)
        if cands:
            pid = _pick(cands, match_date)
            if pid is not None:
                return pid
    # Fallback: surname only. tennis-data abbreviates whichever given name
    # it holds, so "Osorio M." (Maria Camila) cannot match Sackmann's
    # "Camila Osorio" on initial. Surname plus activity is enough when one
    # player clearly dominates.
    surnames = index.get("__surnames__") or {}
    for surname, _initial in key_variants_td(raw_name):
        cands = surnames.get(surname)
        if cands:
            pid = _pick(cands, match_date)
            if pid is not None:
                return pid
    return None


def canonical_names(con: sqlite3.Connection, tour: str) -> dict:
    """player_id -> the name Sackmann uses. Rows we append must carry this,
    not tennis-data's "Sinner J.", or the next import indexes junk."""
    out = {}
    q = """SELECT winner_id, winner_name, MAX(tourney_date) FROM matches
           WHERE tour=? AND tourney_id NOT LIKE 'TD%' GROUP BY winner_id
           UNION ALL
           SELECT loser_id, loser_name, MAX(tourney_date) FROM matches
           WHERE tour=? AND tourney_id NOT LIKE 'TD%' GROUP BY loser_id"""
    best = {}
    for pid, name, d in con.execute(q, (tour, tour)):
        if pid is None or not name:
            continue
        if pid not in best or d > best[pid]:
            best[pid], out[pid] = d, name
    return out


def existing_match_dates(con, tour: str) -> int:
    row = con.execute(
        "SELECT MAX(tourney_date) FROM matches WHERE tour=?", (tour,)).fetchone()
    return row[0] or 0


def existing_pairs(con, tour: str, since: int) -> dict:
    """(winner_id, loser_id) -> [tourney_date, ...] for de-duplication and
    for attaching odds to matches already present."""
    out: dict[tuple[int, int], list] = {}
    for wid, lid, d, key in con.execute(
            """SELECT winner_id, loser_id, tourney_date, match_key FROM matches
               WHERE tour=? AND tourney_date>=?""", (tour, since)):
        out.setdefault((wid, lid), []).append((d, key))
    return out


# -------------------------------------------------------------- import

def read_workbook(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _odds_for(row) -> tuple[float | None, float | None, str | None]:
    for src, wc, lc in (("pinnacle", "PSW", "PSL"), ("average", "AvgW", "AvgL"),
                        ("bet365", "B365W", "B365L")):
        ow, ol = row.get(wc), row.get(lc)
        if implied_probability(ow, ol) is not None:
            return float(ow), float(ol), src
    return None, None, None


def import_workbook(con: sqlite3.Connection, path: str, tour: str,
                    append_new: bool = True, backfill_odds: bool = True,
                    verbose: bool = True) -> dict:
    """Import one season workbook. Returns counters."""
    ensure_schema(con)
    df = read_workbook(path)
    index = build_player_index(con, tour)
    canonical = canonical_names(con, tour)
    latest = existing_match_dates(con, tour)
    pairs = existing_pairs(con, tour, since=0)

    stats = dict(rows=len(df), appended=0, odds_attached=0,
                 unmatched=0, skipped_existing=0, no_odds=0)
    new_rows, unmatched_rows, odds_updates = [], [], []

    for _, r in df.iterrows():
        w_raw, l_raw = r.get("Winner"), r.get("Loser")
        if not isinstance(w_raw, str) or not isinstance(l_raw, str):
            continue
        try:
            d = date_int(r["Date"])
        except Exception:
            continue

        wid = resolve(index, w_raw, d)
        lid = resolve(index, l_raw, d)
        ow, ol, osrc = _odds_for(r)
        if ow is None:
            stats["no_odds"] += 1

        if wid is None or lid is None:
            which = "winner" if wid is None else "loser"
            if wid is None and lid is None:
                which = "both"
            unmatched_rows.append((tour, d, str(r.get("Tournament") or ""),
                                   w_raw, l_raw,
                                   normalise_surface(r.get("Surface")),
                                   f"unresolved {which}", os.path.basename(path)))
            stats["unmatched"] += 1
            continue

        # already in the DB?
        hit = None
        for sd, key in pairs.get((wid, lid), []):
            if within_window(d, sd):
                hit = key
                break
        if hit:
            stats["skipped_existing"] += 1
            if backfill_odds and ow is not None:
                odds_updates.append((ow, ol, osrc, hit))
            continue

        if not append_new or d <= latest:
            continue

        new_rows.append((
            f"{tour}-TD{d}-{wid}-{lid}", f"TD{d}", str(r.get("Tournament") or ""),
            normalise_surface(r.get("Surface")), _level(r.get("Series")), d,
            0, wid, canonical.get(wid, str(w_raw)), lid,
            canonical.get(lid, str(l_raw)), str(r.get("Comment") or ""),
            _best_of(r), str(r.get("Round") or ""), None,
            _int(r.get("WRank")), _int(r.get("LRank")), tour, ow, ol, osrc,
        ))
        stats["appended"] += 1

    if unmatched_rows:
        con.executemany(
            """INSERT OR IGNORE INTO feed_unmatched
               (tour, match_date, tourney_name, winner_raw, loser_raw,
                surface, reason, source_file) VALUES (?,?,?,?,?,?,?,?)""",
            unmatched_rows)
    if odds_updates:
        con.executemany(
            """UPDATE matches SET odds_winner=?, odds_loser=?, odds_source=?
               WHERE match_key=? AND odds_winner IS NULL""", odds_updates)
        stats["odds_attached"] = con.total_changes and len(odds_updates)
    if new_rows:
        cols = ("match_key,tourney_id,tourney_name,surface,tourney_level,"
                "tourney_date,match_num,winner_id,winner_name,loser_id,"
                "loser_name,score,best_of,round,minutes,winner_rank,"
                "loser_rank,tour,odds_winner,odds_loser,odds_source")
        con.executemany(
            f"INSERT OR IGNORE INTO matches ({cols}) VALUES "
            f"({','.join('?' * 21)})", new_rows)
    con.commit()

    if verbose:
        print(f"  {os.path.basename(path):18} {tour}  rows {stats['rows']:5}  "
              f"new {stats['appended']:4}  odds {stats['odds_attached']:5}  "
              f"existing {stats['skipped_existing']:5}  "
              f"unmatched {stats['unmatched']:4}")
    return stats


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _best_of(r):
    s = str(r.get("Best of") or "").strip()
    return _int(s) or 3


def _level(series):
    """tennis-data Series -> Sackmann tourney_level."""
    s = str(series or "").strip().lower()
    if "grand slam" in s:
        return "G"
    if "masters 1000" in s or s == "masters":
        return "M"
    if "masters cup" in s or "finals" in s:
        return "F"
    if s.startswith("international") or "atp" in s or s in ("250", "500"):
        return "A"
    if s.startswith("wta") or "premier" in s:
        return "P"
    return "A"


# ----------------------------------------------------------------- cli

def refresh(db_path: str | None = None, years=None, tours=("ATP", "WTA"),
            local_only: bool = False, verbose: bool = True) -> dict:
    """Download (unless local_only) and import each season workbook."""
    paths.ensure_dirs()
    db_path = db_path or paths.DB
    years = years or paths.ODDS_YEARS
    con = sqlite3.connect(db_path)
    totals: dict = {}
    for tour in tours:
        for year in years:
            local = paths.odds_path(year, tour)
            if not local_only:
                try:
                    local = download_season(year, tour)
                except Exception as e:  # offline or site moved
                    if verbose:
                        print(f"  download failed {tour} {year}: {e}")
            if not os.path.exists(local):
                continue
            s = import_workbook(con, local, tour, verbose=verbose)
            for k, v in s.items():
                totals[k] = totals.get(k, 0) + v
    con.close()
    return totals


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-only", action="store_true",
                    help="import workbooks already in data/odds, no download")
    ap.add_argument("--years", type=int, nargs="*", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    t = refresh(db_path=a.db, years=a.years, local_only=a.local_only)
    print("totals:", t)
