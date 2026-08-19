"""Benchmark vs the bookmakers, both tours.

tennis-data.co.uk gives one row per completed tour match with closing
odds (Pinnacle PSW/PSL preferred, market average as fallback). We:

1. Walk forward through the Sackmann DB, storing OUR pre-match
   probability for every 2024+ tour match (rating uses only earlier
   matches, as always).
2. Convert bookmaker odds to a fair probability (strip the margin:
   pW = (1/oW) / (1/oW + 1/oL)).
3. Join the two datasets on (winner surname+initial, loser
   surname+initial, date within 16 days) since tennis-data uses exact
   match dates and Sackmann uses tournament start dates.
4. Score both on the SAME matched matches. No cherry-picking.
"""

from __future__ import annotations

import math
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict

import pandas as pd

import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import paths
from namematch import implied_probability
from elo import EloEngine
from glicko2 import Glicko2Engine

DB = paths.DB
# data/odds/{year}.xlsx (ATP) and data/odds/{year}_WTA.xlsx (WTA).
# Not committed: download from tennis-data.co.uk/alldata.php, or let
# src/feed_tennisdata.py fetch them.
FILES = {tour: paths.odds_files(tour, existing_only=False) for tour in ("ATP", "WTA")}
TEST_FROM = 20240101
TOP_LEVELS = ("G", "M", "A", "F", "O", "P", "PM", "I", "W")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def key_td(name: str) -> tuple[str, str]:
    """tennis-data style: 'De Minaur A.' -> ('de minaur', 'a')"""
    toks = strip_accents(str(name)).replace(".", "").strip().lower().split()
    if len(toks) >= 2 and len(toks[-1]) <= 2:
        return " ".join(toks[:-1]), toks[-1][0]
    return " ".join(toks), ""


def key_sack(name: str) -> tuple[str, str]:
    """Sackmann style: 'Alex De Minaur' -> ('de minaur', 'a')"""
    toks = strip_accents(str(name)).strip().lower().split()
    if len(toks) >= 2:
        return " ".join(toks[1:]), toks[0][0]
    return " ".join(toks), ""


def our_predictions(tour: str) -> dict:
    """Walk forward; store P(winner) for 2024+ top-level matches."""
    con = sqlite3.connect(DB)
    rows = con.execute(
        """SELECT tourney_date, surface, winner_id, loser_id,
                  winner_name, loser_name, tourney_level
           FROM matches WHERE winner_id IS NOT NULL AND tour=?
           ORDER BY tourney_date, tourney_id, match_num""", (tour,)).fetchall()
    con.close()
    engines = {"elo": EloEngine(), "glicko": Glicko2Engine()}
    preds = defaultdict(list)  # (wkey, lkey) -> [(date, p_elo, p_glicko)]
    for date, surface, wid, lid, wn, ln, lvl in rows:
        if lvl == "D":
            continue
        if lvl in TOP_LEVELS and date >= TEST_FROM:
            pe = engines["elo"].predict(wid, lid, surface or "")
            pg = engines["glicko"].predict(wid, lid, surface or "")
            preds[(key_sack(wn), key_sack(ln))].append((date, pe, pg))
        for e in engines.values():
            e.update(wid, lid, surface or "", date)
    return preds


def date_int(ts) -> int:
    return ts.year * 10000 + ts.month * 100 + ts.day


def days_apart(a: int, b: int) -> int:
    from datetime import date
    da = date(a // 10000, (a // 100) % 100, a % 100)
    db = date(b // 10000, (b // 100) % 100, b % 100)
    return abs((da - db).days)


def run_from_db(tour: str) -> bool:
    """Score against odds stored in the DB by the feed importer.

    Returns False if no odds are present, so the caller falls back to
    reading the xlsx workbooks directly.
    """
    con = sqlite3.connect(paths.DB)
    n = con.execute(
        "SELECT COUNT(*) FROM matches WHERE tour=? AND odds_winner IS NOT NULL",
        (tour,)).fetchone()[0]
    if not n:
        con.close()
        return False

    engines = {"elo": EloEngine(), "glicko": Glicko2Engine()}
    rows = con.execute(
        """SELECT tourney_date, surface, winner_id, loser_id, tourney_level,
                  odds_winner, odds_loser
           FROM matches WHERE tour=? AND winner_id IS NOT NULL
           ORDER BY tourney_date, tourney_id, match_num""", (tour,)).fetchall()
    con.close()

    m = {"book": [], "elo": [], "glicko": []}
    for date, surface, wid, lid, lvl, ow, ol in rows:
        if lvl == "D":
            continue
        if lvl in TOP_LEVELS and date >= TEST_FROM and ow and ol:
            p_book = implied_probability(ow, ol)
            if p_book is not None:
                m["book"].append(p_book)
                m["elo"].append(engines["elo"].predict(wid, lid, surface or ""))
                m["glicko"].append(engines["glicko"].predict(wid, lid, surface or ""))
        for e in engines.values():
            e.update(wid, lid, surface or "", date)

    print(f"\n=== {tour}: {len(m['book'])} matches with odds in database ===")
    _report(m)
    return True


def _report(m: dict) -> None:
    for name in ("book", "elo", "glicko"):
        ps = m[name]
        if not ps:
            continue
        acc = sum(p > 0.5 for p in ps) / len(ps)
        ll = sum(-math.log(min(max(p, 1e-9), 1 - 1e-9)) for p in ps) / len(ps)
        label = {"book": "bookmakers (de-vigged)",
                 "elo": "our Elo", "glicko": "our Glicko-2"}[name]
        print(f"  {label:24} acc {acc:.4f}  logloss {ll:.4f}")


def run(tour: str) -> None:
    if run_from_db(tour):
        return
    print(f"\n(no odds in database for {tour}; reading workbooks)")
    preds = our_predictions(tour)
    frames = [pd.read_excel(f) for f in FILES[tour]]
    td = pd.concat(frames, ignore_index=True)

    matched = 0
    used = set()
    m = {"book": [], "elo": [], "glicko": []}
    for _, r in td.iterrows():
        ow, ol = r.get("PSW"), r.get("PSL")
        if not (ow and ol and ow == ow and ol == ol):
            ow, ol = r.get("AvgW"), r.get("AvgL")
        if not (ow and ol and ow == ow and ol == ol and ow > 1 and ol > 1):
            continue
        k = (key_td(r["Winner"]), key_td(r["Loser"]))
        cands = preds.get(k)
        if not cands:
            continue
        d = date_int(r["Date"])
        best = None
        for i, (sd, pe, pg) in enumerate(cands):
            gap = days_apart(d, sd)
            if gap <= 16 and (best is None or gap < best[0]) and (k, i) not in used:
                best = (gap, i, pe, pg)
        if best is None:
            continue
        used.add((k, best[1]))
        p_book = (1 / ow) / (1 / ow + 1 / ol)  # de-vigged, P(actual winner)
        m["book"].append(p_book)
        m["elo"].append(best[2])
        m["glicko"].append(best[3])
        matched += 1

    print(f"\n=== {tour}: {matched} matches matched "
          f"(of {len(td)} tennis-data rows) ===")
    for name in ("book", "elo", "glicko"):
        ps = m[name]
        acc = sum(p > 0.5 for p in ps) / len(ps)
        ll = sum(-math.log(min(max(p, 1e-9), 1 - 1e-9)) for p in ps) / len(ps)
        label = {"book": "bookmakers (de-vigged)",
                 "elo": "our Elo", "glicko": "our Glicko-2"}[name]
        print(f"  {label:24} acc {acc:.4f}  logloss {ll:.4f}")


if __name__ == "__main__":
    run("ATP")
    run("WTA")
