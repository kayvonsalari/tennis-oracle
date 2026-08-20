# Aristos Tennis Oracle

A deterministic ATP and WTA match predictor. The verdict comes from
surface-adjusted Glicko-2 ratings replayed over more than a million
matches — **no LLM sits in the decision path**, so a prediction is
reproducible and costs nothing. The engine speaks the A2A
(Agent-to-Agent) protocol, both as a server and as a client, and there is
a Streamlit UI on top of it.

Ratings are replayed strictly in date order, so a rating used to predict a
match only ever sees matches played before it. Every prediction carries
its own uncertainty: Glicko-2 tracks a rating deviation per player that
grows during inactivity, and `src/predictor.py` turns that into explicit
caveats rather than hiding it.

## Setup

The match data is **not** in this repo and is not optional — `load_data.py`
exits with `missing CSV directory` without it. Run the whole block from a
clean clone:

```bash
pip install -r requirements.txt

mkdir -p data
curl -L -o data/atp.zip https://codeload.github.com/Kadantte/tennis_atp/zip/refs/heads/master
curl -L -o data/wta.zip https://codeload.github.com/chestnutforty/tennis_wta/zip/refs/heads/master
cd data && unzip -q atp.zip && unzip -q wta.zip && cd ..

python src/load_data.py      # builds data/tennis.db, a few minutes
python src/refresh.py        # pulls current results and odds
```

Those two GitHub repos are **frozen mirrors**. Jeff Sackmann removed the
original `tennis_atp` and `tennis_wta` repos from GitHub in 2026, so do not
waste time looking for them — the forks above are what remains. They are
frozen at ATP 2026-06-01 and WTA 2026-04-27; `src/refresh.py` is what keeps
the ratings current past those dates, by importing tennis-data.co.uk's
weekly publication.

`load_data.py` with no arguments reads `data/tennis_atp-master` and
`data/tennis_wta-master`, which is exactly where the two zips unpack. Pass
directories explicitly if you put them somewhere else. Everything under
`data/` is gitignored. `TENNIS_ORACLE_DB` and `TENNIS_ORACLE_ODDS` override
the database and odds locations (see `src/paths.py`).

## Running it

```bash
streamlit run src/app.py                                # UI
python src/predictor.py "Player A" "Player B" Clay ATP  # one prediction
python src/a2a_server.py                                # A2A endpoint, port 9999
```

The predictor CLI takes `player_a player_b surface tour` and prints the
full prediction JSON: predicted winner, win probability, both players'
ratings with uncertainty, head-to-head, recent form, rest days, serve
statistics, caveats, and the data cutoff.

The A2A server publishes its card at `/.well-known/agent-card.json` and
accepts JSON-RPC at `/`. Requests are natural language of the form
`"[ATP|WTA] PlayerA vs PlayerB on <surface>"`. **Every request needs the
header `A2A-Version: 1.0`** — without it the a2a-sdk assumes protocol 0.3
and refuses. Set `NARRATE=1` (plus `ANTHROPIC_API_KEY`) to have
`src/narrator.py` write the summary in prose; the structured artifact is
identical either way.

`src/a2a_client.py` is the outbound half and can call **any** A2A agent, not
just this one — it is written against plain JSON-RPC so it does not need a
matching SDK version on the other end:

```bash
python src/a2a_client.py http://127.0.0.1:9999                       # show the card
python src/a2a_client.py http://127.0.0.1:9999 "Alcaraz vs Sinner on clay"
```

## What is in `src/`

| Module | Role |
|---|---|
| `paths.py` | every path derived from the repo root; no machine-specific absolutes |
| `load_data.py` | Sackmann CSVs → SQLite, both tours, player IDs namespaced |
| `glicko2.py` | Glicko-2 engine — **the judge**; overall + per-surface, blended 50/50 |
| `elo.py` | surface-adjusted Elo, kept as the comparison baseline |
| `evidence.py` | plain SQL evidence: player lookup, H2H, form, rest, serve, match level |
| `predictor.py` | assembles the structured prediction and its rule-generated caveats |
| `narrator.py` | optional prose layer; never changes a number or a verdict |
| `a2a_server.py` | A2A server (a2a-sdk v1.x on FastAPI), port 9999 |
| `a2a_client.py` | A2A client for calling any agent, over raw JSON-RPC |
| `namematch.py` | reconciles tennis-data.co.uk names and dates with Sackmann's |
| `feed_tennisdata.py` | imports current results and closing odds; logs unmatched rows |
| `refresh.py` | download → import → sanity-check, in one command |
| `backtest.py` | walk-forward accuracy and log loss vs the ranking baseline |
| `bakeoff.py` | Elo vs Glicko-2 on an identical test window |
| `benchmark_odds.py` | scores the engine against de-vigged closing odds |

## Accuracy

Walk-forward over 2024–2026, no lookahead. Measured, not estimated —
reproduce with `src/backtest.py`, `src/bakeoff.py` and
`src/benchmark_odds.py`.

| Level | Accuracy | Closing odds | Ranking baseline |
|---|---|---|---|
| ATP tour | 65.4% | 67.9% | 62.9% |
| WTA tour | 65.7% | 67.2% | 63.5% |
| ATP Challenger | 64.4% | not priced | 62.8% |
| WTA ITF | 70.5–70.8% | not priced | 64.2% |

The market is the ceiling because it prices injuries and withdrawals that
no historical database contains.

## Licence

The match data is **Jeff Sackmann's, under CC BY-NC-SA 4.0**: attribution
required, share-alike, **non-commercial only**. That constraint propagates
through the ratings derived from it and therefore covers this entire
project. Portfolio and demonstration use is fine. Anything a client pays
for is not.

Odds come from tennis-data.co.uk and are used for benchmarking only. Their
redistribution terms have not been reviewed, which is why no odds files are
committed here — `src/feed_tennisdata.py` downloads them to your own
machine.

This is not betting advice.

## Known gaps

- `tests/` and `src/app.py` are not yet in this repo, so the
  `streamlit run src/app.py` line above documents the intended entry point
  rather than a file you can run today.
- No live results feed beyond tennis-data.co.uk's weekly publication, so
  ratings lag the tour by up to a week.
- The narrator is optional and off unless both `NARRATE=1` and
  `ANTHROPIC_API_KEY` are set; the template renderer is the default path.
