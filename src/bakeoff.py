"""Bake-off: Elo vs Glicko-2 on the identical walk-forward test.

Same rules for every contender:
- ratings built from matches strictly before each predicted match
- test window: tour-level matches 2024-01-01 onward, Davis Cup excluded
- Challenger matches feed ratings but are never predicted

Metrics:
- accuracy: share of matches where the favourite by the model won
- log loss: quality of the probabilities (lower better, coin flip 0.693)
- calibration: when the model says 70%, does the favourite win ~70%?
"""

from __future__ import annotations

import math
import sqlite3
import sys
import time

from elo import EloEngine
from glicko2 import Glicko2Engine

DB = "/home/claude/tennis-agent/data/tennis.db"
TEST_FROM = 20240101


def load_rows():
    con = sqlite3.connect(DB)
    rows = con.execute(
        """SELECT tourney_date, surface, winner_id, loser_id,
                  winner_rank, loser_rank, tourney_level
           FROM matches WHERE winner_id IS NOT NULL AND loser_id IS NOT NULL
           ORDER BY tourney_date, tourney_id, match_num"""
    ).fetchall()
    con.close()
    return rows


def evaluate(name, engine, rows):
    t0 = time.time()
    n = hits = 0
    ll = 0.0
    buckets = [[0, 0] for _ in range(5)]  # 50-60,60-70,70-80,80-90,90-100
    for date, surface, wid, lid, wr, lr, lvl in rows:
        if lvl == "D":
            continue  # Davis Cup: neither predicted nor rated
        tour = lvl in ("G", "M", "A", "F", "O")
        if tour and date >= TEST_FROM:
            p = engine.predict(wid, lid, surface or "")
            fav = max(p, 1 - p)
            fav_won = (p >= 0.5)
            hits += fav_won
            ll += -math.log(min(max(p, 1e-9), 1 - 1e-9))
            b = min(int((fav - 0.5) * 10), 4)
            buckets[b][0] += fav_won
            buckets[b][1] += 1
            n += 1
        engine.update(wid, lid, surface or "", date)
    acc, lls = hits / n, ll / n
    print(f"{name:34} acc {acc:.4f}  logloss {lls:.4f}  ({time.time()-t0:.0f}s, n={n})")
    return name, acc, lls, buckets


def main():
    rows = load_rows()
    results = []
    results.append(evaluate("Elo (current: 50/50 surface)", EloEngine(), rows))
    results.append(evaluate("Glicko-2 (50/50, RD inflation)", Glicko2Engine(), rows))
    results.append(evaluate("Glicko-2 (no inactivity inflation)",
                            Glicko2Engine(use_inflation=False), rows))
    results.append(evaluate("Glicko-2 (overall only)",
                            Glicko2Engine(surface_weight=0.0), rows))

    print("\nbaseline reminder: higher-ranked-player-wins = 0.641")
    print("\ncalibration (bucketed by stated favourite probability):")
    print(f"{'model':34} " + "  ".join(f"{lo}-{lo+10}%" for lo in (50, 60, 70, 80, 90)))
    for name, _, _, b in results:
        cells = []
        for won, tot in b:
            cells.append(f"{100*won/tot:5.1f}({tot})" if tot else "   -  ")
        print(f"{name:34} " + " ".join(cells))


if __name__ == "__main__":
    main()
