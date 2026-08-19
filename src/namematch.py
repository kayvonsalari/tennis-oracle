"""Name and match reconciliation between tennis-data.co.uk and Sackmann.

The two sources disagree in three ways:
  1. Name format: tennis-data writes "Sinner J.", Sackmann "Jannik Sinner".
  2. Accents: "Carlos Alcaraz" vs "Carlos Alcaráz" appear inconsistently.
  3. Dates: tennis-data records the actual match date, Sackmann the
     tournament start date, so the same match can differ by up to ~2 weeks.

Everything here is pure string/date handling with no database access, so
it can be unit tested without fixtures.
"""

from __future__ import annotations

import unicodedata
from datetime import date

# A match in the two sources may be up to this many days apart.
DATE_WINDOW_DAYS = 16


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s))
                   if unicodedata.category(c) != "Mn")


def key_td(name: str) -> tuple[str, str]:
    """tennis-data.co.uk format -> (surname, first initial).

    'De Minaur A.'   -> ('de minaur', 'a')
    'Wolf J.J.'      -> ('wolf', 'j')      multiple initials
    'Struff J.L.'    -> ('struff', 'j')
    'O Connell C.'   -> ('o connell', 'c')
    """
    raw = strip_accents(name).replace("-", " ").strip().lower()
    # a trailing token ending in "." is an abbreviated given name, however
    # long: "Wang Xin." (Xinyu), "Fernandez L.A.", "Struff J.L."
    truncated = []
    parts = raw.split()
    while len(parts) > 1 and parts[-1].endswith("."):
        truncated.insert(0, parts.pop())
    toks = " ".join(parts).replace(".", " ").split()
    initials = [t[0] for t in " ".join(truncated).replace(".", " ").split() if t]
    if not initials:
        # no dots at all: strip trailing single letters
        while len(toks) > 1 and len(toks[-1]) == 1:
            initials.insert(0, toks.pop()[0])
    if not toks:
        return "", ""
    return " ".join(toks), (initials[0] if initials else "")


def key_sack(name: str) -> tuple[str, str]:
    """Sackmann format -> (surname, first initial), primary interpretation.

    'Alex De Minaur' -> ('de minaur', 'a')

    Sackmann writes "FirstName(s) Surname(s)" with no marker between them,
    so "Jan Lennard Struff" is genuinely ambiguous: the surname could be
    "Lennard Struff" or "Struff". Use keys_sack() to get every plausible
    reading; this function returns the everything-after-the-first-token
    interpretation only.
    """
    toks = strip_accents(name).replace("-", " ").strip().lower().split()
    if not toks:
        return "", ""
    if len(toks) >= 2:
        return " ".join(toks[1:]), toks[0][0]
    return " ".join(toks), ""


def keys_sack(name: str) -> set:
    """All plausible (surname, initial) readings of a Sackmann name.

    'Jan Lennard Struff' -> {('lennard struff','j'), ('struff','j')}
    'Christopher Oconnell' -> {('oconnell','c'), ('o connell','c')}

    Indexing every reading is what lets a tennis-data name find its match
    regardless of where the first/last name boundary actually falls.
    """
    toks = strip_accents(name).replace("-", " ").strip().lower().split()
    if not toks:
        return set()
    if len(toks) == 1:
        return {(toks[0], "")}
    initial = toks[0][0]
    out = set()
    # every split point: surname = last 1, last 2, ... tokens
    for i in range(1, len(toks)):
        out.add((" ".join(toks[i:]), initial))
    # squashed and de-squashed variants for apostrophe names
    # ("O Connell" in one source, "Oconnell" in the other)
    for surname, ini in list(out):
        out.add((surname.replace(" ", ""), ini))
        if surname.startswith(("o ", "d ", "l ")):
            out.add((surname.replace(" ", "", 1), ini))
        for pre in ("o", "d", "l"):
            if surname.startswith(pre) and len(surname) > 3:
                out.add((pre + " " + surname[len(pre):], ini))
    return out


def key_variants_td(name: str) -> list:
    """Lookup keys to try for a tennis-data name, best first."""
    surname, initial = key_td(name)
    out = [(surname, initial)]
    squashed = surname.replace(" ", "")
    if squashed != surname:
        out.append((squashed, initial))
    if " " in surname:                      # try last token alone
        out.append((surname.split()[-1], initial))
    return out


def normalise_key(k: tuple[str, str]) -> tuple[str, str]:
    """Collapse hyphen/space differences so the two formats meet."""
    surname, initial = k
    return surname.replace("-", " ").strip(), initial


def date_int(value) -> int:
    """Accept datetime/date/pandas Timestamp/YYYYMMDD int -> YYYYMMDD int."""
    if hasattr(value, "year"):
        return value.year * 10000 + value.month * 100 + value.day
    return int(value)


def days_apart(a: int, b: int) -> int:
    da = date(a // 10000, (a // 100) % 100, a % 100)
    db = date(b // 10000, (b // 100) % 100, b % 100)
    return abs((da - db).days)


def within_window(a: int, b: int, window: int = DATE_WINDOW_DAYS) -> bool:
    return days_apart(a, b) <= window


SURFACE_MAP = {"hard": "Hard", "clay": "Clay", "grass": "Grass",
               "carpet": "Carpet"}


def normalise_surface(s) -> str | None:
    if s is None:
        return None
    return SURFACE_MAP.get(str(s).strip().lower())


def implied_probability(odds_winner: float, odds_loser: float) -> float | None:
    """De-vigged probability that the actual winner wins.

    Bookmaker odds include a margin, so 1/oW + 1/oL > 1. Normalising by
    their sum removes it. Returns None for unusable odds.
    """
    try:
        ow, ol = float(odds_winner), float(odds_loser)
    except (TypeError, ValueError):
        return None
    if not (ow > 1 and ol > 1):
        return None
    return (1 / ow) / (1 / ow + 1 / ol)
