"""A2A server: exposes the tennis predictor as a standard Agent-to-Agent
endpoint using the official a2a-sdk (v1.x, protobuf-based).

What another agent sees:
- GET /.well-known/agent-card.json  -> who we are, what we can do
- POST JSON-RPC message/send        -> ask "Alcaraz vs Sinner on clay",
                                       get structured prediction back

The verdict inside is deterministic Elo. No LLM is involved in serving
a prediction, so a request costs nothing.
"""

from __future__ import annotations

import json
import os
import re

from a2a.helpers import new_data_part, new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI

from narrator import narrate
from predictor import predict

# ---------------------------------------------------------------- card

from a2a.types import AgentInterface

CARD = AgentCard(
    name="aristos-tennis-oracle",
    description=(
        "Deterministic ATP and WTA match predictor covering tour, Challenger "
        "and ITF levels. Surface-adjusted Glicko-2 over 1M+ matches. "
        "Measured accuracy (walk-forward, 2024-2026): ATP tour 65.4% vs "
        "closing odds 67.9%; WTA tour 65.7% vs 67.2%; ATP Challenger 64.4%; "
        "WTA ITF 70.5-70.8%, where no bookmaker line exists. Rank-based "
        "baseline is 62.8-64.2%. Returns win probability, ratings with "
        "uncertainty, head-to-head, form and explicit caveats. Math judges; "
        "no LLM in the verdict path. Data: Jeff Sackmann's datasets "
        "(CC BY-NC-SA 4.0) plus tennis-data.co.uk. Non-commercial use only."
    ),
    version="0.1.0",
    supported_interfaces=[
        AgentInterface(url="http://127.0.0.1:9999/", protocol_binding="JSONRPC",
                       protocol_version="1.0")
    ],
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["text/plain"],
    default_output_modes=["application/json", "text/plain"],
    skills=[
        AgentSkill(
            id="predict_match",
            name="Predict ATP match outcome",
            description=(
                "Input: '[ATP|WTA] PlayerA vs PlayerB on <surface>' "
                "(tour defaults to ATP, surface to hard). Lower-tier players "
                "are supported: accuracy is highest on WTA ITF events. "
                "Output: JSON with predicted_winner, win_probability, level, "
                "ratings with uncertainty, evidence and caveats."
            ),
            tags=["tennis", "prediction", "glicko", "elo", "sports",
                  "atp", "wta", "challenger", "itf"],
            examples=[
                "Carlos Alcaraz vs Jannik Sinner on clay",
                "WTA Aryna Sabalenka vs Iga Swiatek on hard",
                "WTA Lamis Alhussein Abdel Aziz vs Sandra Samir on clay",
            ],
        )
    ],
)

# ------------------------------------------------------------ executor

PATTERN = re.compile(
    r"^\s*(?:(?P<tour>atp|wta)\s*:?\s+)?(?P<a>.+?)\s+(?:vs\.?|versus)\s+(?P<b>.+?)"
    r"(?:\s+on\s+(?P<surface>hard|clay|grass|carpet))?\s*$",
    re.IGNORECASE,
)


class TennisOracleExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # SDK v1 lifecycle: the queue must receive a Task object before
        # any status update, or the dispatcher rejects the whole run.
        await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        query = context.get_user_input() or ""
        m = PATTERN.match(query)
        if not m:
            await updater.complete(
                message=new_text_message(
                    "Could not parse request. Expected: 'PlayerA vs PlayerB on <surface>'.",
                    task_id=context.task_id, context_id=context.context_id,
                )
            )
            return

        result = predict(m["a"].strip(), m["b"].strip(),
                         (m["surface"] or "hard"), (m["tour"] or "ATP"))

        if "error" in result:
            await updater.failed(
                message=new_text_message(
                    result["error"],
                    task_id=context.task_id, context_id=context.context_id,
                )
            )
            return

        await updater.add_artifact(
            parts=[new_data_part(result)],
            name="prediction",
        )
        if os.environ.get("NARRATE") == "1":
            # Prose for humans. The artifact above is unchanged either way,
            # and the verdict is already fixed: this only rewords it.
            summary = narrate(result)
        else:
            summary = (
                f"{result['predicted_winner']} to win "
                f"({result['win_probability']:.0%}) on {result['surface']} "
                f"({result['tour']}, {result['level']} level). "
                f"Deterministic Glicko-2. Data through {result['data_through']}."
            )
        await updater.complete(
            message=new_text_message(
                summary, task_id=context.task_id, context_id=context.context_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


# -------------------------------------------------------------- wiring

def build_app() -> FastAPI:
    handler = DefaultRequestHandlerV2(
        agent_executor=TennisOracleExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=CARD,
    )
    app = FastAPI(title="aristos-tennis-oracle")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=CARD),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app


app = build_app()


@app.on_event("startup")
def _warm():
    """Replay Elo once at boot so the first request isn't slow."""
    from predictor import engine
    engine("ATP")
    engine("WTA")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9999, log_level="warning")
