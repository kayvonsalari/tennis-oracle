"""Turn a prediction into prose. The narrator never decides anything.

Doctrine: math judges, LLM writes. The verdict, the probability and the
caveats are already fixed by the time this module sees them. Its only job
is to say them in English, including the parts that are inconvenient.

Costs nothing when ANTHROPIC_API_KEY is unset: the template path produces
a complete, honest rendering with zero API calls. That is the default, and
the A2A server only calls this at all when NARRATE=1.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

MODEL = "claude-haiku-4-5-20251001"   # cheapest tier; verify at docs.claude.com
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_TOKENS = 500
TIMEOUT = 30

SYSTEM = """You are writing a short brief about a tennis match prediction.

The prediction is already decided by a deterministic rating system. You are \
not deciding anything. Rules, all strict:

1. Never change or soften the predicted winner or the probability. Do not \
hedge a 65% into "roughly two thirds" or "a slight edge" — quote it.
2. Every number you write must appear verbatim in the JSON you are given. \
Do not compute new numbers, averages, or percentages of your own.
3. Include at least one caveat from the caveats list, in plain words.
4. If the evidence points the other way from the verdict — head-to-head \
favours the other player, better recent form, more rest — say so plainly. \
Do not omit it to make the prediction sound stronger.
5. Three short paragraphs at most. No headings, no bullet points, no \
preamble like "Here is a brief". Start with the prediction itself.
6. Plain English. No betting language, no advice about wagering."""


class NarratorUnavailable(RuntimeError):
    """Raised only by narrate_llm(); narrate() falls back instead."""


# --------------------------------------------------------------- helpers

def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _fmt_record(rec: dict, a: str, b: str) -> str:
    return f"{rec.get('a_wins', 0)}-{rec.get('b_wins', 0)}"


def _evidence_lines(pred: dict) -> list[str]:
    ev = pred.get("evidence") or {}
    names = list((pred.get("ratings") or {}).keys())
    a = names[0] if names else "player A"
    b = names[1] if len(names) > 1 else "player B"
    out = []
    h2h = ev.get("h2h_overall") or {}
    if h2h.get("a_wins") or h2h.get("b_wins"):
        out.append(f"Head to head: {a} leads {_fmt_record(h2h, a, b)}"
                   if h2h.get("a_wins", 0) > h2h.get("b_wins", 0)
                   else f"Head to head: {b} leads "
                        f"{h2h.get('b_wins', 0)}-{h2h.get('a_wins', 0)}")
    hs = ev.get("h2h_surface") or {}
    if hs.get("a_wins") or hs.get("b_wins"):
        out.append(f"On {pred.get('surface', 'this surface').lower()}: "
                   f"{hs.get('a_wins', 0)}-{hs.get('b_wins', 0)} to {a}")
    fa, fb = ev.get("form_a") or {}, ev.get("form_b") or {}
    if fa.get("last_n"):
        out.append(f"Recent form: {a} {fa.get('wins', 0)}-{fa.get('losses', 0)}"
                   f" in last {fa.get('last_n')}, "
                   f"{b} {fb.get('wins', 0)}-{fb.get('losses', 0)}"
                   f" in last {fb.get('last_n', 0)}")
    ra, rb = ev.get("rest_days_a"), ev.get("rest_days_b")
    if ra is not None and rb is not None:
        out.append(f"Days since last recorded match: {a} {ra}, {b} {rb}")
    sa, sb = ev.get("serve_pts_won_a"), ev.get("serve_pts_won_b")
    if sa and sb:
        out.append(f"Service points won: {a} {sa}%, {b} {sb}%")
    return out


# -------------------------------------------------------------- template

def narrate_template(pred: dict) -> str:
    """Deterministic rendering. No API, no key, no cost, never fails."""
    if "error" in pred:
        return str(pred["error"])
    winner = pred.get("predicted_winner", "?")
    prob = pred.get("win_probability", 0)
    ratings = pred.get("ratings") or {}
    parts = [
        f"{winner} to win, {_pct(prob)} on {pred.get('surface', 'unknown')} "
        f"({pred.get('tour', 'ATP')})."
    ]
    rbits = [f"{n} {r.get('overall')} ± {r.get('uncertainty')}"
             for n, r in ratings.items()]
    if rbits:
        parts.append("Ratings: " + "; ".join(rbits) + ".")
    ev = _evidence_lines(pred)
    if ev:
        parts.append(" ".join(e + "." for e in ev))
    cav = pred.get("caveats") or []
    if cav:
        parts.append("Caveats: " + " ".join(c.rstrip(".") + "." for c in cav))
    parts.append(f"Ratings use matches up to {pred.get('data_through', 'unknown')}.")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ llm

def _call_api(key: str, prompt: str) -> str:
    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"content-type": "application/json",
                 "x-api-key": key,
                 "anthropic-version": API_VERSION})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    chunks = [c.get("text", "") for c in data.get("content", [])
              if c.get("type") == "text"]
    text = "".join(chunks).strip()
    if not text:
        raise NarratorUnavailable("empty response")
    return text


def narrate_llm(pred: dict, api_key: str | None = None, retry: bool = True) -> str:
    """One API call (plus at most one retry). Raises if unavailable."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise NarratorUnavailable("ANTHROPIC_API_KEY not set")
    prompt = ("Write the brief for this prediction. Use only these numbers:\n\n"
              + json.dumps(pred, indent=2))
    try:
        return _call_api(key, prompt)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            KeyError, TimeoutError) as e:
        if not retry:
            raise NarratorUnavailable(str(e)) from e
    try:
        return _call_api(key, prompt)
    except Exception as e:  # noqa: BLE001 - fallback is the point
        raise NarratorUnavailable(str(e)) from e


# ----------------------------------------------------------------- main

def narrate(pred: dict, api_key: str | None = None) -> str:
    """Prose for a prediction. Uses the API when a key exists, otherwise
    the template. Never raises, never blocks a prediction."""
    if "error" in pred:
        return str(pred["error"])
    try:
        return narrate_llm(pred, api_key=api_key)
    except NarratorUnavailable:
        return narrate_template(pred)


def available(api_key: str | None = None) -> bool:
    return bool(api_key or os.environ.get("ANTHROPIC_API_KEY"))


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from predictor import predict

    a = sys.argv[1] if len(sys.argv) > 1 else "Carlos Alcaraz"
    b = sys.argv[2] if len(sys.argv) > 2 else "Jannik Sinner"
    s = sys.argv[3] if len(sys.argv) > 3 else "Clay"
    t = sys.argv[4] if len(sys.argv) > 4 else "ATP"
    print(narrate(predict(a, b, s, t)))
