"""Evidence gatherers: plain database queries, no LLM anywhere.

Each function returns facts about a matchup. They inform the narrator
and are exposed in the structured output; they do not change the verdict
(the Elo probability is the verdict).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime


def _today_int() -> int:
    t = date.today()
    return t.year * 10000 + t.month * 100 + t.day


def find_player(con: sqlite3.Connection, name: str,
                tour: str = "ATP") -> tuple[int, str] | None:
    """Resolve a name to (player_id, canonical_name) within one tour.
    Case-insensitive; prefers the player with the most recent match."""
    for col in ("winner", "loser"):
        row = con.execute(
            f"""SELECT {col}_id, {col}_name, MAX(tourney_date) d FROM matches
                WHERE LOWER({col}_name) = LOWER(?) AND tour = ?
                GROUP BY {col}_id ORDER BY d DESC LIMIT 1""",
            (name, tour),
        ).fetchone()
        if row and row[0] is not None:
            return row[0], row[1]
    row = con.execute(
        """SELECT winner_id, winner_name, MAX(tourney_date) d FROM matches
           WHERE LOWER(winner_name) LIKE '%' || LOWER(?) || '%' AND tour = ?
           GROUP BY winner_id ORDER BY d DESC LIMIT 1""",
        (name, tour),
    ).fetchone()
    return (row[0], row[1]) if row and row[0] is not None else None


def head_to_head(con, a: int, b: int, surface: str | None = None) -> dict:
    q = """SELECT winner_id, COUNT(*) FROM matches
           WHERE ((winner_id=? AND loser_id=?) OR (winner_id=? AND loser_id=?))"""
    args = [a, b, b, a]
    if surface:
        q += " AND surface=?"
        args.append(surface)
    q += " GROUP BY winner_id"
    wins = dict(con.execute(q, args).fetchall())
    return {"a_wins": wins.get(a, 0), "b_wins": wins.get(b, 0)}


def recent_form(con, pid: int, n: int = 10) -> dict:
    rows = con.execute(
        """SELECT winner_id, tourney_date, surface FROM matches
           WHERE (winner_id=? OR loser_id=?) AND tourney_level != 'D'
           ORDER BY tourney_date DESC, tourney_id DESC, match_num DESC LIMIT ?""",
        (pid, pid, n),
    ).fetchall()
    wins = sum(1 for w, _, _ in rows if w == pid)
    return {"last_n": len(rows), "wins": wins, "losses": len(rows) - wins}


def surface_record(con, pid: int, surface: str, since: int) -> dict:
    rows = con.execute(
        """SELECT winner_id FROM matches
           WHERE (winner_id=? OR loser_id=?) AND surface=? AND tourney_date>=?
           AND tourney_level != 'D'""",
        (pid, pid, surface, since),
    ).fetchall()
    wins = sum(1 for (w,) in rows if w == pid)
    return {"matches": len(rows), "wins": wins}


def days_since_last_match(con, pid: int) -> int | None:
    row = con.execute(
        "SELECT MAX(tourney_date) FROM matches WHERE winner_id=? OR loser_id=?",
        (pid, pid),
    ).fetchone()
    if not row or not row[0]:
        return None
    last = datetime.strptime(str(row[0]), "%Y%m%d").date()
    return (date.today() - last).days


def serve_hold_pct(con, pid: int, surface: str, since: int) -> float | None:
    """Service points won percentage on this surface (proxy for hold strength)."""
    row = con.execute(
        """SELECT SUM(CASE WHEN winner_id=? THEN w_1stWon + w_2ndWon ELSE l_1stWon + l_2ndWon END),
                  SUM(CASE WHEN winner_id=? THEN w_svpt ELSE l_svpt END)
           FROM matches
           WHERE (winner_id=? OR loser_id=?) AND surface=? AND tourney_date>=?
           AND w_svpt IS NOT NULL""",
        (pid, pid, pid, pid, surface, since),
    ).fetchone()
    won, total = row
    return round(100 * won / total, 1) if won and total else None


TOP_LEVELS = ("G", "M", "A", "F", "O", "P", "PM", "I", "W")
CHALLENGER_LEVELS = ("C",)


def player_level(con, pid: int, lookback: int = 20) -> str:
    """Where this player actually competes: tour, challenger, or itf.

    Based on the most common level across their recent matches, not the
    single latest one, so a qualifier's one tour appearance does not
    reclassify them.
    """
    rows = con.execute(
        """SELECT tourney_level FROM matches
           WHERE (winner_id=? OR loser_id=?) AND tourney_level IS NOT NULL
           ORDER BY tourney_date DESC, tourney_id DESC LIMIT ?""",
        (pid, pid, lookback)).fetchall()
    if not rows:
        return "unknown"
    counts = {"tour": 0, "challenger": 0, "itf": 0}
    for (lvl,) in rows:
        if lvl in TOP_LEVELS:
            counts["tour"] += 1
        elif lvl in CHALLENGER_LEVELS:
            counts["challenger"] += 1
        else:
            counts["itf"] += 1
    return max(counts, key=counts.get)


def match_level(con, a: int, b: int) -> str:
    """Level of a hypothetical meeting: the higher of the two players'."""
    order = {"tour": 3, "challenger": 2, "itf": 1, "unknown": 0}
    la, lb = player_level(con, a), player_level(con, b)
    return la if order[la] >= order[lb] else lb


def gather(con, a: int, b: int, surface: str) -> dict:
    """All evidence for the matchup, one call."""
    year_ago = _today_int() - 20000  # ~2 years, integer date arithmetic is fine here
    return {
        "h2h_overall": head_to_head(con, a, b),
        "h2h_surface": head_to_head(con, a, b, surface),
        "form_a": recent_form(con, a),
        "form_b": recent_form(con, b),
        "surface_a": surface_record(con, a, surface, year_ago),
        "surface_b": surface_record(con, b, surface, year_ago),
        "rest_days_a": days_since_last_match(con, a),
        "rest_days_b": days_since_last_match(con, b),
        "serve_pts_won_a": serve_hold_pct(con, a, surface, year_ago),
        "serve_pts_won_b": serve_hold_pct(con, b, surface, year_ago),
    }
