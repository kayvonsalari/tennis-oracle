"""Glicko-2 engine, adapted for tennis (match-by-match updates).

Glicko-2 = Elo plus two extra numbers per player:
- RD (rating deviation): how uncertain the rating is. Shrinks with
  matches, GROWS with inactivity. This is the honest version of our
  "rating may be stale" caveat.
- volatility: how erratic the player's results have been.

Implementation follows Glickman's published spec (glicko.net/glicko2).
One rating period = one week; a player's RD inflates for each idle week.
Surface handling mirrors the Elo engine: overall + per-surface ratings,
prediction is a 50/50 blend.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

SCALE = 173.7178
START_R, START_RD, START_VOL = 1500.0, 350.0, 0.06
TAU = 0.5          # volatility constraint (Glickman recommends 0.3-1.2)
MAX_RD = 350.0
SURFACES = ("Hard", "Clay", "Grass", "Carpet")


@dataclass
class GRating:
    mu: float = 0.0                          # (r-1500)/SCALE
    phi: float = START_RD / SCALE
    sigma: float = START_VOL
    last_week: int | None = None

    @property
    def rating(self) -> float:
        return self.mu * SCALE + 1500.0

    @property
    def rd(self) -> float:
        return self.phi * SCALE


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi ** 2))


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_sigma(sigma: float, delta: float, phi: float, v: float) -> float:
    """Illinois-method iteration from the Glicko-2 paper."""
    a = math.log(sigma * sigma)

    def f(x):
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / (TAU * TAU)

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU
    fA, fB = f(A), f(B)
    for _ in range(100):
        if abs(B - A) < 1e-6:
            break
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC
    return math.exp(A / 2.0)


def _inflate(r: GRating, week: int) -> None:
    """RD grows during inactivity: one sigma-step per idle week."""
    if r.last_week is not None and week > r.last_week:
        idle = week - r.last_week
        r.phi = min(math.sqrt(r.phi ** 2 + (r.sigma ** 2) * idle), MAX_RD / SCALE)


def _update_pair(w: GRating, l: GRating, week: int) -> None:
    _inflate(w, week)
    _inflate(l, week)
    for me, opp, score in ((w, l, 1.0), (l, w, 0.0)):
        g = _g(opp.phi)
        E = _E(me.mu, opp.mu, opp.phi)
        v = 1.0 / (g * g * E * (1.0 - E))
        delta = v * g * (score - E)
        sigma2 = _new_sigma(me.sigma, delta, me.phi, v)
        phi_star = math.sqrt(me.phi ** 2 + sigma2 ** 2)
        me._new_phi = 1.0 / math.sqrt(1.0 / phi_star ** 2 + 1.0 / v)
        me._new_mu = me.mu + me._new_phi ** 2 * g * (score - E)
        me._new_sigma = sigma2
    for r in (w, l):
        r.mu, r.phi, r.sigma = r._new_mu, r._new_phi, r._new_sigma
        r.last_week = week


def _week(date: int) -> int:
    """Approximate week index from YYYYMMDD (fine for RD inflation)."""
    y, m, d = date // 10000, (date // 100) % 100, date % 100
    return y * 52 + (m - 1) * 4 + d // 8


@dataclass
class GPlayer:
    overall: GRating = field(default_factory=GRating)
    by_surface: dict = field(default_factory=lambda: {s: GRating() for s in SURFACES})


class Glicko2Engine:
    def __init__(self, surface_weight: float = 0.5, use_inflation: bool = True):
        self.players: dict[int, GPlayer] = defaultdict(GPlayer)
        self.sw = surface_weight
        self.use_inflation = use_inflation

    def _pwin(self, ra: GRating, rb: GRating) -> float:
        return 1.0 / (1.0 + math.exp(
            -_g(math.sqrt(ra.phi ** 2 + rb.phi ** 2)) * (ra.mu - rb.mu)))

    def predict(self, a: int, b: int, surface: str) -> float:
        pa, pb = self.players[a], self.players[b]
        p = self._pwin(pa.overall, pb.overall)
        if surface in SURFACES and self.sw > 0:
            ps = self._pwin(pa.by_surface[surface], pb.by_surface[surface])
            p = (1 - self.sw) * p + self.sw * ps
        return p

    def update(self, winner: int, loser: int, surface: str, date: int) -> None:
        wk = _week(date) if self.use_inflation else 0
        w, l = self.players[winner], self.players[loser]
        _update_pair(w.overall, l.overall, wk)
        if surface in SURFACES:
            _update_pair(w.by_surface[surface], l.by_surface[surface], wk)
