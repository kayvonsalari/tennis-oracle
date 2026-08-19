# Aristos Tennis Oracle

Deterministic ATP + WTA match predictor exposed as an A2A (Agent-to-Agent)
protocol server. Doctrine: **math judges, LLM narrates** — the verdict is
surface-adjusted Glicko-2; no LLM sits in the decision path, so a
prediction costs zero API tokens.

## Validated numbers (walk-forward, 2024–2026, no lookahead)

| Tour | This engine | Naive baseline (rank) | Bookmakers (de-vigged) |
|------|------------|----------------------|------------------------|
| ATP  | 65.4% / logloss 0.613 | 64.1% | 68.2% / 0.589 |
| WTA  | 65.8% / logloss 0.612 | 63.5% | 67.0% / 0.593 |

Glicko-2 beat plain Elo on probability quality (paired bootstrap on
10,000 matches: logloss edge +0.0016, 95% CI [0.0003, 0.0031]) and adds
a per-player uncertainty (RD) that grows with inactivity — powering
honest caveats in every response.

## Layout
- `src/load_data.py`   — Sackmann CSVs -> SQLite (both tours, ID-namespaced)
- `src/elo.py`         — Elo engine (kept for benchmarking)
- `src/glicko2.py`     — Glicko-2 engine (the judge)
- `src/evidence.py`    — deterministic evidence queries (H2H, form, serve)
- `src/predictor.py`   — structured prediction JSON
- `src/a2a_server.py`  — A2A server (official a2a-sdk v1.x, FastAPI)
- `src/backtest.py`, `src/bakeoff.py`, `src/benchmark_odds.py` — validation

## Run
```
pip install a2a-sdk fastapi uvicorn sse_starlette pandas
python src/load_data.py          # builds data/tennis.db (~1.06M matches)
python src/a2a_server.py         # card: /.well-known/agent-card.json, port 9999
```
Request (JSON-RPC, note the required `A2A-Version: 1.0` header):
`"[ATP|WTA] PlayerA vs PlayerB on <surface>"`

## Data & licence
Jeff Sackmann's tennis datasets (via surviving forks; originals removed
from his GitHub as of Aug 2026): **CC BY-NC-SA 4.0 — attribution
required, non-commercial only.** Odds benchmark: tennis-data.co.uk.
Data cutoffs: ATP 2026-06-01, WTA 2026-04-27 (swap the feed to refresh).

## Not yet built
- LLM narrator (deliberately last; Haiku recommended, Fable 5 optional
  for tactical read of Match Charting shot data)
- Live results feed (tennis-data.co.uk updates ~weekly and is current)
- A2A client for calling other agents
