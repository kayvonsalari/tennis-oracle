"""Surface-adjusted Elo engine. This is the judge: pure arithmetic, no LLM.

Design (follows the approach Jeff Sackmann and FiveThirtyEight wrote up):
- Every player carries an overall rating plus one rating per surface.
- After each match, both update. The prediction blends them 50/50,
  which outperforms either alone (surface-only starves on data for
  players who rarely play a surface; overall-only ignores clay/grass
  specialists).
- K-factor decays with matches played: new players move fast, veterans
  move slowly. K = base / (matches + offset)^shape, the standard
  tennis-Elo form.
- Ratings are replayed chronologically from 1978, so a rating at any
  date only uses information available before that date. No lookahead.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

START_RATING = 1500.0
K_BASE, K_OFFSET, K_SHAPE = 250.0, 5.0, 0.4
SURFACES = ("Hard", "Clay", "Grass", "Carpet")


def k_factor(n_matches: int) -> float:
    return K_BASE / (n_matches + K_OFFSET) ** K_SHAPE


def expected(r_a: float, r_b: float) -> float:
    """Probability A beats B given ratings."""
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))


@dataclass
class PlayerState:
    overall: float = START_RATING
    n_overall: int = 0
    by_surface: dict = field(default_factory=lambda: {s: START_RATING for s in SURFACES})
    n_surface: dict = field(default_factory=lambda: {s: 0 for s in SURFACES})
    last_match_date: int = 0
    name: str = ""


class EloEngine:
    def __init__(self):
        self.players: dict[int, PlayerState] = defaultdict(PlayerState)

    def predict(self, a: int, b: int, surface: str) -> float:
        """P(a beats b) on this surface. Blend of overall and surface Elo."""
        pa, pb = self.players[a], self.players[b]
        p_overall = expected(pa.overall, pb.overall)
        if surface in SURFACES:
            p_surf = expected(pa.by_surface[surface], pb.by_surface[surface])
            return 0.5 * p_overall + 0.5 * p_surf
        return p_overall

    def update(self, winner: int, loser: int, surface: str, date: int,
               w_name: str = "", l_name: str = "") -> None:
        w, l = self.players[winner], self.players[loser]
        if w_name:
            w.name = w_name
        if l_name:
            l.name = l_name

        p = expected(w.overall, l.overall)
        w.overall += k_factor(w.n_overall) * (1 - p)
        l.overall -= k_factor(l.n_overall) * (1 - p)
        w.n_overall += 1
        l.n_overall += 1

        if surface in SURFACES:
            ps = expected(w.by_surface[surface], l.by_surface[surface])
            w.by_surface[surface] += k_factor(w.n_surface[surface]) * (1 - ps)
            l.by_surface[surface] -= k_factor(l.n_surface[surface]) * (1 - ps)
            w.n_surface[surface] += 1
            l.n_surface[surface] += 1

        w.last_match_date = date
        l.last_match_date = date


def replay(db_path: str, until_date: int | None = None) -> EloEngine:
    """Replay all matches chronologically to build current ratings."""
    con = sqlite3.connect(db_path)
    q = """SELECT tourney_date, surface, winner_id, loser_id, winner_name, loser_name
           FROM matches WHERE winner_id IS NOT NULL AND loser_id IS NOT NULL
           AND tourney_level != 'D'"""  # Davis Cup: motivation noise, hurts accuracy
    if until_date:
        q += f" AND tourney_date < {int(until_date)}"
    q += " ORDER BY tourney_date, tourney_id, match_num"
    eng = EloEngine()
    for date, surface, wid, lid, wn, ln in con.execute(q):
        eng.update(wid, lid, surface or "", date, wn, ln)
    con.close()
    return eng
