"""Speak A2A outbound: fetch another agent's card and send it a task.

The server side (src/a2a_server.py) lets others call us. This is the other
half. Written against a2a-sdk v1.x, whose wire format has three details
that older documentation gets wrong and that cost real debugging time:

  1. the "A2A-Version: 1.0" header is mandatory; without it the server
     assumes 0.3 and refuses
  2. RPC method names are "SendMessage", "GetTask" and so on, not the
     older "message/send"
  3. message parts are {"text": ...} objects, and the role enum is a
     string like "ROLE_USER"

Implemented over plain JSON-RPC rather than the SDK's client classes so
that it works against any A2A endpoint without version-matching the SDK
on both ends.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

PROTOCOL_VERSION = "1.0"
CARD_PATH = "/.well-known/agent-card.json"
TIMEOUT = 120


class A2AError(RuntimeError):
    pass


def _post(url: str, payload: dict, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "A2A-Version": PROTOCOL_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise A2AError(f"HTTP {e.code} from {url}: {e.read()[:300]!r}") from e
    except urllib.error.URLError as e:
        raise A2AError(f"cannot reach {url}: {e.reason}") from e


def fetch_card(base_url: str, timeout: int = 30) -> dict:
    """Read an agent's card: who it is and what it can do."""
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", CARD_PATH.lstrip("/"))
    req = urllib.request.Request(url, headers={"A2A-Version": PROTOCOL_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise A2AError(f"no agent card at {url} (HTTP {e.code})") from e
    except urllib.error.URLError as e:
        raise A2AError(f"cannot reach {url}: {e.reason}") from e


def describe(card: dict) -> str:
    """One-screen summary of a card, for humans."""
    lines = [f"{card.get('name', '?')} v{card.get('version', '?')}",
             (card.get("description") or "").strip()]
    for s in card.get("skills", []) or []:
        lines.append(f"  skill: {s.get('id')} — {s.get('name')}")
        for ex in (s.get("examples") or [])[:3]:
            lines.append(f"    e.g. {ex}")
    return "\n".join(x for x in lines if x)


def _endpoint(base_url: str, card: dict | None) -> str:
    """Prefer the JSONRPC interface the card advertises; else the base URL."""
    for iface in (card or {}).get("supportedInterfaces", []) or []:
        binding = str(iface.get("protocolBinding", "")).upper()
        if "JSONRPC" in binding and iface.get("url"):
            return iface["url"]
    return base_url


def ask(base_url: str, text: str, card: dict | None = None,
        timeout: int = TIMEOUT) -> tuple[str, list]:
    """Send one question, return (summary text, structured artifacts).

    Artifacts are the machine-readable payload — for a prediction agent,
    the full JSON verdict. The summary is the human sentence.
    """
    url = _endpoint(base_url, card)
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {"message": {
            "messageId": str(uuid.uuid4()),
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }},
    }
    resp = _post(url, payload, timeout=timeout)
    if "error" in resp:
        err = resp["error"]
        raise A2AError(f"agent returned error {err.get('code')}: "
                       f"{err.get('message')}")

    result = resp.get("result") or {}
    task = result.get("task") or result
    state = (task.get("status") or {}).get("state")

    # The completion text lives at status.message.parts (SDK v1). Older
    # drafts of the spec called this "update"; accept either.
    status = task.get("status") or {}
    holder = status.get("message") or status.get("update") or {}
    summary = " ".join(p.get("text", "") for p in holder.get("parts", [])).strip()

    artifacts = []
    for art in task.get("artifacts", []) or []:
        for part in art.get("parts", []) or []:
            if "data" in part:
                artifacts.append(part["data"])
            elif "text" in part and not summary:
                summary = part["text"]

    if state and state not in ("TASK_STATE_COMPLETED", "completed"):
        raise A2AError(f"task ended in state {state}: {summary or 'no detail'}")
    return summary, artifacts


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__)
        print("usage:\n"
              "  python src/a2a_client.py <base_url>              # show card\n"
              "  python src/a2a_client.py <base_url> \"<question>\"  # ask")
        return 2
    base = argv[1]
    try:
        card = fetch_card(base)
    except A2AError as e:
        print(f"error: {e}")
        return 1
    if len(argv) < 3:
        print(describe(card))
        return 0
    try:
        summary, artifacts = ask(base, argv[2], card=card)
    except A2AError as e:
        print(f"error: {e}")
        return 1
    print(summary or "(no summary returned)")
    for a in artifacts:
        print()
        print(json.dumps(a, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
