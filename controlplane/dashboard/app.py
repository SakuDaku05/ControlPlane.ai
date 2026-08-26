"""
HITL Dashboard — live view of everything the Dynamic Action Engine and the
three micro-agents are doing (spec 3.3: "fires a webhook to the HITL
dashboard").

No `websockets`/`wsproto` package is installable in this sandbox, so this
uses Server-Sent Events (sse-starlette, already available) instead of a
WebSocket — one-directional server push is all a live telemetry feed
needs anyway. A background task drains the `actions`, `escalations`, and
`agent-results` Redis streams (via their own dashboard consumer groups —
see event_bus.ensure_groups) and fans each new event out to every
connected browser tab.
"""
from __future__ import annotations

import asyncio
import json
import logging
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from ..config import settings

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from ..event_bus import new_connected_bus
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from demo.load_test import run_simulation

logger = logging.getLogger("controlplane.dashboard")

STATIC_DIR = Path(__file__).parent / "static"

_subscribers: set[asyncio.Queue] = set()
_recent: list[dict] = []  # small ring buffer so a newly-opened tab isn't blank
_RECENT_MAX = 200
_stats = {
    "total_requests": 0,
    "low": 0,
    "medium": 0,
    "high": 0,
    "session_cost_usd": 0.0,
}

_sim_task: asyncio.Task | None = None
_sim_stop_event: asyncio.Event | None = None

AGENT_NAMES = ["cost", "performance", "responsibility"]
AGENT_REPORT_PROFILES = {
    "cost": {
        "title": "CostAgent Budget & Spend Report",
        "focus": "token usage, estimated spend, budget burn, rate limits, and cost anomalies",
        "sections": "Executive Summary, Spend & Token Analysis, Budget Risk, and Cost Controls",
    },
    "performance": {
        "title": "PerformanceAgent Grounding & Hallucination Report",
        "focus": "semantic grounding scores, unsupported claims, hallucination risk, and response quality",
        "sections": "Executive Summary, Grounding Analysis, Hallucination Findings, and Quality Recommendations",
    },
    "responsibility": {
        "title": "ResponsibilityAgent Safety & Policy Report",
        "focus": "PII exposure, toxicity, policy violations, safety risk, and required mitigations",
        "sections": "Executive Summary, Safety Findings, Policy Impact, and Remediation Actions",
    },
}
_agent_stats: dict = {
    name: {
        "total": 0,
        "low": 0,
        "medium": 0,
        "high": 0,
        "scores": [],        # last 100 scores for avg
        "recent": [],        # last 20 findings
    }
    for name in AGENT_NAMES
}


def _broadcast(payload: dict) -> None:
    _recent.append(payload)
    if len(_recent) > _RECENT_MAX:
        _recent.pop(0)
    for q in list(_subscribers):
        q.put_nowait(payload)


def _bump_stats(payload: dict) -> None:
    if payload.get("kind") == "action":
        _stats["total_requests"] += 1
        level = payload.get("risk_level", "LOW_RISK").split("_")[0].lower()
        if level in ("low", "medium", "high"):
            _stats[level] += 1


async def _consume_loop() -> None:
    bus = None
    while True:
        try:
            if bus is None:
                bus = await new_connected_bus()
                await bus.ensure_groups()
                logger.info("dashboard consumer loop started")

            actions = await bus.consume_actions(f"cp-agents:dashboard-actions", count=20, block_ms=1500)
            for eid, decision in actions:
                payload = {
                    "kind": "action",
                    "trace_id": decision.trace_id,
                    "risk_level": decision.risk_level.value,
                    "action": decision.action.value,
                    "reason": decision.reason,
                    "contributing_agents": decision.contributing_agents,
                    "latency_impact": decision.latency_impact,
                    "created_at": decision.created_at,
                }
                _bump_stats(payload)
                _broadcast(payload)
                await bus.ack_action("cp-agents:dashboard-actions", eid)

            results = await bus.consume_findings("cp-agents:dashboard-results", count=20, block_ms=100)
            for eid, finding in results:
                payload = {
                    "kind": "finding",
                    "trace_id": finding.trace_id,
                    "agent": finding.agent,
                    "risk_level": finding.risk_level.value,
                    "score": finding.score,
                    "reason": finding.reason,
                    "details": finding.details,
                    "created_at": finding.created_at,
                }
                _broadcast(payload)
                # ── per-agent tracking ──────────────────────────────────
                akey = finding.agent.lower().replace("agent", "").strip()
                for k in AGENT_NAMES:
                    if k in akey:
                        akey = k
                        break
                if akey in _agent_stats:
                    s = _agent_stats[akey]
                    s["total"] += 1
                    level = finding.risk_level.value.split("_")[0].lower()
                    if level in ("low", "medium", "high"):
                        s[level] += 1
                    if finding.score is not None:
                        s["scores"].append(finding.score)
                        if len(s["scores"]) > 100:
                            s["scores"].pop(0)
                    rec = {
                        "trace_id": finding.trace_id,
                        "event_id": finding.event_id,
                        "agent": finding.agent,
                        "risk_level": finding.risk_level.value,
                        "score": finding.score,
                        "reason": finding.reason,
                        "details": finding.details,
                        "created_at": finding.created_at,
                    }
                    s["recent"].append(rec)
                    if len(s["recent"]) > 100:
                        s["recent"].pop(0)
                # ──────────────────────────────────────────────────────
                await bus.ack_finding("cp-agents:dashboard-results", eid)

            escalations = await bus.consume_escalations("cp-agents:dashboard-escalations", count=20, block_ms=100)
            for eid, esc in escalations:
                related_findings = [
                    finding
                    for agent_stats in _agent_stats.values()
                    for finding in agent_stats["recent"]
                    if finding["trace_id"] == esc.trace_id
                ]
                related_actions = [
                    action
                    for action in reversed(_recent)
                    if action.get("kind") == "action" and action.get("trace_id") == esc.trace_id
                ]
                payload = {
                    "kind": "escalation",
                    "escalation_id": esc.escalation_id,
                    "trace_id": esc.trace_id,
                    "risk_level": esc.risk_level.value,
                    "reason": esc.reason,
                    "snippet": esc.snippet,
                    "prompt": esc.prompt,
                    "findings": related_findings,
                    "actions": related_actions,
                    "created_at": esc.created_at,
                }
                _broadcast(payload)
                await bus.ack_escalation("cp-agents:dashboard-escalations", eid)

            session_cost = await bus.get_session_total_cost()
            _stats["session_cost_usd"] = round(session_cost, 5)
        except Exception:
            logger.exception("dashboard consume loop error; reconnecting in 2s")
            if bus:
                try:
                    await bus.close()
                except Exception:
                    pass
                bus = None
            await asyncio.sleep(2.0)


async def index(request: Request):
    return FileResponse(STATIC_DIR / "index.html")


async def summary(request: Request) -> JSONResponse:
    return JSONResponse({"stats": _stats, "recent": _recent[-50:]})


async def resolve_escalation(request: Request) -> JSONResponse:
    trace_id = request.path_params["trace_id"]
    bus = await new_connected_bus()
    try:
        # Mark as resolved in Redis
        await bus._pub.sadd("cp:resolved_traces", trace_id)
        payload = {"kind": "resolved", "trace_id": trace_id}
        _broadcast(payload)
        return JSONResponse({"status": "resolved"})
    finally:
        await bus.close()


async def start_simulation(request: Request) -> JSONResponse:
    global _sim_task, _sim_stop_event
    if _sim_task is not None and not _sim_task.done():
        return JSONResponse({"status": "already_running"})
    
    _sim_stop_event = asyncio.Event()
    _sim_task = asyncio.create_task(run_simulation(_sim_stop_event, rate=10))
    return JSONResponse({"status": "started"})


async def stop_simulation(request: Request) -> JSONResponse:
    global _sim_task, _sim_stop_event
    if _sim_task is not None and not _sim_task.done():
        _sim_stop_event.set()
        return JSONResponse({"status": "stopping"})
    return JSONResponse({"status": "not_running"})


async def events(request: Request) -> EventSourceResponse:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.add(queue)

    async def gen():
        try:
            for payload in _recent[-30:]:
                yield {"event": "cp", "data": json.dumps(payload)}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": "cp", "data": json.dumps(payload)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": json.dumps({"stats": _stats})}
        finally:
            _subscribers.discard(queue)

    return EventSourceResponse(gen())


@asynccontextmanager
async def lifespan(app: Starlette):
    task = asyncio.create_task(_consume_loop())
    try:
        yield
    finally:
        task.cancel()


async def agent_stats(request: Request) -> JSONResponse:
    """Return per-agent stats derived from the in-memory ring buffer."""
    result = {}
    for name, s in _agent_stats.items():
        avg_score = round(sum(s["scores"]) / len(s["scores"]), 3) if s["scores"] else None
        result[name] = {
            "total": s["total"],
            "low": s["low"],
            "medium": s["medium"],
            "high": s["high"],
            "avg_score": avg_score,
            "recent": list(reversed(s["recent"])),
        }
    # also return global session stats for context
    result["_global"] = _stats
    return JSONResponse(result)


async def simulator(request: Request):
    return FileResponse(STATIC_DIR / "simulator.html")


async def generate_agent_report(request: Request) -> JSONResponse:
    agent_id = request.path_params["agent_id"]
    if agent_id not in _agent_stats:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
        
    s = _agent_stats[agent_id]
    findings = list(reversed(s["recent"]))
    
    if not findings:
        return JSONResponse({"report": f"No findings available for the {agent_id} agent in the current session. Run a simulation first!"})

    profile = AGENT_REPORT_PROFILES[agent_id]
    prompt = (
        f"You are generating the {profile['title']}. Analyze findings only through the lens of this agent's purpose: {profile['focus']}.\n"
        f"Here are the most recent {len(findings)} findings from the current live session:\n\n"
        f"{json.dumps(findings, indent=2)}\n\n"
        f"Return a concise, professional markdown report titled exactly '# {profile['title']}'. "
        f"Use these sections: {profile['sections']}. "
        f"Do not discuss metrics outside this agent's responsibility, invent data, or include conversational filler."
    )
    
    body = {
        "model": settings.report_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # We strip trailing / from base url just in case
            base_url = settings.openai_base_url.rstrip("/")
            url = f"{base_url}/chat/completions" if "/v1" in base_url else f"{base_url}/v1/chat/completions"
            if "groq" not in base_url.lower():
                  body["model"] = "gpt-4o-mini"
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            report = data.get("choices", [{}])[0].get("message", {}).get("content", "Error generating report.")
            if not report.lstrip().startswith(f"# {profile['title']}"):
                report = f"# {profile['title']}\n\n{report.lstrip()}"
            return JSONResponse({"title": profile["title"], "report": report})
    except Exception as e:
        logger.exception("Failed to generate report")
        return JSONResponse({"error": str(e)}, status_code=500)


app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/simulator", simulator, methods=["GET"]),
        Route("/api/summary", summary, methods=["GET"]),
        Route("/api/agents/stats", agent_stats, methods=["GET"]),
        Route("/api/agents/{agent_id}/report", generate_agent_report, methods=["POST"]),
        Route("/api/resolve/{trace_id}", resolve_escalation, methods=["POST"]),
        Route("/api/simulation/start", start_simulation, methods=["POST"]),
        Route("/api/simulation/stop", stop_simulation, methods=["POST"]),
        Route("/api/events", events, methods=["GET"]),
    ],
    lifespan=lifespan,
)
