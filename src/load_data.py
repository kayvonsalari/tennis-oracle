"""Load Jeff Sackmann ATP match files into a local SQLite database.

Data source: fork Kadantte/tennis_atp (synced 2026-06-08).
Licence: CC BY-NC-SA 4.0 — attribution required, non-commercial only.

The feed is swappable: anything that writes rows into the `matches`
table with the same columns can replace it later.
"""

import csv
import glob
import os
import sqlite3
import sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_key      TEXT PRIMARY KEY,   -- tourney_id + match_num
    tourney_id     TEXT,
    tourney_name   TEXT,
    surface        TEXT,               -- Hard / Clay / Grass / Carpet
    tourney_level  TEXT,               -- G=Slam, M=Masters, A=Tour, D=Davis, F=Finals, C=Challenger
    tourney_date   INTEGER,            -- YYYYMMDD
    match_num      INTEGER,
    winner_id      INTEGER,
    winner_name    TEXT,
    loser_id       INTEGER,
    loser_name     TEXT,
    score          TEXT,
    best_of        INTEGER,
    round          TEXT,
    minutes        INTEGER,
    w_svpt INTEGER, w_1stIn INTEGER, w_1stWon INTEGER, w_2ndWon INTEGER,
    w_SvGms INTEGER, w_bpSaved INTEGER, w_bpFaced INTEGER,
    l_svpt INTEGER, l_1stIn INTEGER, l_1stWon INTEGER, l_2ndWon INTEGER,
    l_SvGms INTEGER, l_bpSaved INTEGER, l_bpFaced INTEGER,
    winner_rank INTEGER, loser_rank INTEGER,
    tour TEXT DEFAULT 'ATP'
);
CREATE INDEX IF NOT EXISTS idx_matches_date    ON matches(tourney_date);
CREATE INDEX IF NOT EXISTS idx_matches_winner  ON matches(winner_id);
CREATE INDEX IF NOT EXISTS idx_matches_loser   ON matches(loser_id);
CREATE INDEX IF NOT EXISTS idx_matches_surface ON matches(surface);

CREATE TABLE IF NOT EXISTS players (
    player_id  INTEGER PRIMARY KEY,
    name_first TEXT,
    name_last  TEXT,
    hand       TEXT,
    dob        INTEGER,
    country    TEXT
);
"""

COLS = [
    "tourney_id", "tourney_name", "surface", "tourney_level", "tourney_date",
    "match_num", "winner_id", "winner_name", "loser_id", "loser_name",
    "score", "best_of", "round", "minutes",
    "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon", "w_SvGms", "w_bpSaved", "w_bpFaced",
    "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced",
    "winner_rank", "loser_rank",
]


def to_int(v):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def load(data_dir: str, db_path: str, min_year: int = 1978,
         tour_tag: str = "ATP", id_offset: int = 0) -> None:
    """min_year 1978: earliest era with mostly-complete surface data;
    Elo needs long warm-up anyway."""
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    prefix = "wta" if tour_tag == "WTA" else "atp"
    lower = "qual_itf" if tour_tag == "WTA" else "qual_chall"
    tour = sorted(glob.glob(os.path.join(data_dir, f"{prefix}_matches_[12][0-9][0-9][0-9].csv")))
    tour = [f for f in tour if int(f[-8:-4]) >= min_year]
    chall = sorted(glob.glob(os.path.join(data_dir, f"{prefix}_matches_{lower}_*.csv")))
    chall = [f for f in chall if int(f[-8:-4]) >= 1990]  # feeds newcomer ratings
    files = tour + chall
    total = 0
    for f in files:
        with open(f, newline="", encoding="utf-8", errors="replace") as fh:
            rows = []
            for r in csv.DictReader(fh):
                key = f"{tour_tag}-{r['tourney_id']}-{r['match_num']}"
                vals = [key]
                for c in COLS:
                    v = r.get(c)
                    if c in ("surface", "tourney_id", "tourney_name", "tourney_level",
                             "winner_name", "loser_name", "score", "round"):
                        vals.append(v or None)
                    else:
                        iv = to_int(v)
                        if iv is not None and c in ("winner_id", "loser_id"):
                            iv += id_offset
                        vals.append(iv)
                rows.append(vals + [tour_tag])
            con.executemany(
                f"INSERT OR REPLACE INTO matches VALUES ({','.join('?' * (len(COLS) + 2))})",
                rows,
            )
            total += len(rows)
    print(f"loaded {total} matches from {len(files)} files")

    pf = os.path.join(data_dir, f"{prefix}_players.csv")
    with open(pf, newline="", encoding="utf-8", errors="replace") as fh:
        rows = [
            (to_int(r["player_id"]) + id_offset, r["name_first"], r["name_last"],
             r["hand"], to_int(r["dob"]), r["ioc"])
            for r in csv.DictReader(fh)
        ]
    con.executemany("INSERT OR REPLACE INTO players VALUES (?,?,?,?,?,?)", rows)
    print(f"loaded {len(rows)} players")

    con.commit()
    con.close()


if __name__ == "__main__":
    db_path = "/home/claude/tennis-agent/data/tennis.db"
    load("/tmp/atp_data/tennis_atp-master", db_path, tour_tag="ATP")
    load("/tmp/wta_data/tennis_wta-master", db_path, tour_tag="WTA",
         id_offset=100_000_000)
