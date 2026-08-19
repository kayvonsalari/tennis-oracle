"""One command: pull the latest results, import them, prove nothing broke.

    python src/refresh.py              # download + import + check
    python src/refresh.py --local-only # import workbooks already on disk

The sanity check exists because a broken name join fails silently: rows
stop matching, no new matches land, and the ratings quietly freeze while
everything still "works". Checking that the top-rated players have played
recently catches that in one line.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths
from feed_tennisdata import refresh as feed_refresh
from glicko2 import Glicko2Engine

# A broken join shows up as data that stops arriving. tennis-data.co.uk
# publishes weekly, and tours pause, so allow a generous margin.
MAX_DATA_AGE_DAYS = 45


def _to_date(yyyymmdd: int) -> date:
    return datetime.strptime(str(yyyymmdd), "%Y%m%d").date()


def top_players(con: sqlite3.Connection, tour: str, n: int = 10) -> list:
    eng = Glicko2Engine()
    q = """SELECT tourney_date, surface, winner_id, loser_id FROM matches
           WHERE tour=? AND winner_id IS NOT NULL AND tourney_level!='D'
           ORDER BY tourney_date, tourney_id, match_num"""
    for d, s, w, l in con.execute(q, (tour,)):
        eng.update(w, l, s or "", d)
    names = dict(con.execute(
        "SELECT winner_id, winner_name FROM matches WHERE tour=? GROUP BY winner_id",
        (tour,)))
    last = dict(con.execute(
        """SELECT pid, MAX(d) FROM (
             SELECT winner_id pid, tourney_date d FROM matches WHERE tour=?
             UNION ALL
             SELECT loser_id pid, tourney_date d FROM matches WHERE tour=?)
           GROUP BY pid""", (tour, tour)))
    ranked = sorted(eng.players.items(),
                    key=lambda kv: kv[1].overall.rating, reverse=True)[:n]
    return [(pid, names.get(pid, str(pid)), st.overall.rating,
             st.overall.rd, last.get(pid, 0)) for pid, st in ranked]


def sanity_check(con: sqlite3.Connection, verbose: bool = True,
                 max_age_days: int = MAX_DATA_AGE_DAYS) -> bool:
    """Fail loudly if new results are not landing.

    The signal is data recency, NOT who sits at the top of the table.
    Retired players legitimately keep high ratings: Glicko-2 never decays
    a rating, it only widens the uncertainty (RD) during inactivity. So
    Federer and Graf near the top is correct behaviour, not a fault.

    What genuinely indicates a broken join is the newest match in the
    database being old, or no active player appearing near the top.
    """
    ok = True
    today = date.today()
    for tour in ("ATP", "WTA"):
        newest = con.execute(
            "SELECT MAX(tourney_date) FROM matches WHERE tour=?", (tour,)
        ).fetchone()[0]
        if not newest:
            print(f"  FAIL [{tour}]: no matches at all")
            ok = False
            continue
        age = (today - _to_date(newest)).days
        rows = top_players(con, tour)
        active = [r for r in rows
                  if r[4] and (today - _to_date(r[4])).days <= 400]

        if verbose:
            print(f"\n{tour}: newest match {_to_date(newest)} ({age} days ago)")
            print(f"   top 10 by rating, active players marked *")
            for _, name, rating, rd, last in rows[:6]:
                mark = "*" if last and (today - _to_date(last)).days <= 400 else " "
                print(f"   {mark} {rating:7.0f} ± {rd:3.0f}  {name:26} "
                      f"last {_to_date(last)}")

        if age > max_age_days:
            ok = False
            print(f"  FAIL [{tour}]: newest match is {age} days old "
                  f"(limit {max_age_days}). New results are not landing: "
                  f"check the download and the name join.")
        if not active:
            ok = False
            print(f"  FAIL [{tour}]: no currently active player in the top 10. "
                  f"Ratings have probably frozen.")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-only", action="store_true",
                    help="skip downloads; import workbooks already in data/odds")
    ap.add_argument("--years", type=int, nargs="*", default=None)
    ap.add_argument("--skip-check", action="store_true")
    a = ap.parse_args()

    paths.ensure_dirs()
    if not os.path.exists(paths.DB):
        print(f"No database at {paths.DB}. Run src/load_data.py first.")
        return 2

    print("importing tennis-data.co.uk workbooks")
    totals = feed_refresh(years=a.years, local_only=a.local_only)
    rows = totals.get("rows", 0)
    unmatched = totals.get("unmatched", 0)
    rate = (100 * unmatched / rows) if rows else 0
    print(f"\nappended {totals.get('appended', 0)} matches, "
          f"odds on {totals.get('odds_attached', 0)}, "
          f"unmatched {unmatched} ({rate:.1f}%)")
    if rate > 5:
        print(f"  WARNING: unmatched rate above 5%. Inspect feed_unmatched.")

    if a.skip_check:
        return 0
    con = sqlite3.connect(paths.DB)
    ok = sanity_check(con)
    con.close()
    if not ok:
        return 1
    print("\nsanity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
