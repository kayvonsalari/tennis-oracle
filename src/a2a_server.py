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

from predictor import predict

# ---------------------------------------------------------------- card

from a2a.types import AgentInterface

CARD = AgentCard(
    name="aristos-tennis-oracle",
    description=(
        "Deterministic ATP and WTA match predictor. Surface-adjusted Glicko-2 "
        "trained on 1M+ matches (1978-2026). Benchmarked vs bookmakers: "
        "ATP 65.4% (market 68.2%), WTA 65.8% (market 67.0%). Returns win "
        "probability, ratings, head-to-head, form, and honest caveats. "
        "Math judges; no LLM in the verdict path. "
        "Data: Jeff Sackmann's tennis_atp (CC BY-NC-SA 4.0). Non-commercial."
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
                "(tour defaults to ATP, surface to hard). Output: JSON with "
                "predicted_winner, win_probability, ratings, evidence, caveats."
            ),
            tags=["tennis", "prediction", "elo", "sports", "atp"],
            examples=[
                "Carlos Alcaraz vs Jannik Sinner on clay",
                "WTA Aryna Sabalenka vs Iga Swiatek on hard",
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
        summary = (
            f"{result['predicted_winner']} to win "
            f"({result['win_probability']:.0%}) on {result['surface']} "
            f"({result['tour']}). "
            f"Method: deterministic surface Elo. Data through {result['data_through']}."
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
