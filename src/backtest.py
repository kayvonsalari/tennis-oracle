"""Backtest: does surface-Elo beat 'higher-ranked player wins'?

Walk forward through the test window in date order. For each match,
predict with ratings built ONLY from earlier matches, then feed the
result into the engine. No lookahead.

Metrics:
- accuracy: how often the pick is right (baseline must be beaten)
- log loss: punishes confident wrong answers; rewards honest
  probabilities. Lower is better. Coin-flip = 0.693.
"""

from __future__ import annotations

import math
import sqlite3
import sys

import paths
from elo import EloEngine

TEST_FROM = 20240101


def run(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    rows = con.execute(
        """SELECT tourney_date, surface, winner_id, loser_id,
                  winner_name, loser_name, winner_rank, loser_rank
           FROM matches
           WHERE winner_id IS NOT NULL AND loser_id IS NOT NULL
           ORDER BY tourney_date, tourney_id, match_num"""
    ).fetchall()
    con.close()

    eng = EloEngine()
    n = elo_hits = 0
    rank_hits = rank_n = 0
    elo_ll = 0.0

    for date, surface, wid, lid, wn, ln, wr, lr in rows:
        if date >= TEST_FROM:
            p = eng.predict(wid, lid, surface or "")  # P(actual winner wins)
            elo_hits += p > 0.5
            p_c = min(max(p, 1e-9), 1 - 1e-9)
            elo_ll += -math.log(p_c)
            n += 1
            if wr and lr:
                rank_hits += wr < lr  # lower rank number = better player
                rank_n += 1
        eng.update(wid, lid, surface or "", date, wn, ln)

    print(f"test window: {TEST_FROM} onward, {n} matches")
    print(f"baseline (higher rank wins): {rank_hits/rank_n:.3f}  ({rank_n} with ranks)")
    print(f"surface-elo accuracy:        {elo_hits/n:.3f}")
    print(f"surface-elo log loss:        {elo_ll/n:.3f}  (coin flip = 0.693)")

    top = sorted(eng.players.items(), key=lambda kv: kv[1].overall, reverse=True)[:10]
    print("\ncurrent top 10 by overall Elo:")
    for pid, st in top:
        print(f"  {st.overall:7.1f}  {st.name}  "
              f"(H {st.by_surface['Hard']:.0f} / C {st.by_surface['Clay']:.0f} / "
              f"G {st.by_surface['Grass']:.0f})")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else paths.DB)
