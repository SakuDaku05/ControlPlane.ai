"""
The core "fork and forward" logic (spec 2.2 Feature 1 / spec 3.2.1).

"Must fork the payload stream to the evaluation engine while allowing the
primary stream to pass through immediately to the user."

Per request, this module:

  1. Calls the chosen ContentSource (real upstream or the offline Mock)
     and reads text deltas as they arrive.
  2. Accumulates them into a small rolling buffer (config.fast_path_*) and,
     each time that buffer hits a natural boundary (a clause-ending
     punctuation mark, or a max character count), runs the synchronous
     guardrail fast path on it — this is the only thing that can add
     latency to the primary stream, and it's regex-speed by construction.
  3. Publishes every flushed buffer as a RawEvent onto the async event bus
     (a single local XADD — sub-millisecond) so the three background
     micro-agents can pick it up independently; this publish is awaited
     (not fire-and-forget) because it is cheap enough not to matter, and
     awaiting it means we never lose an event to a dropped task.
  4. Before flushing each buffer, checks the Redis "kill flag" — the
     channel through which a slower async agent (grounding check, cost
     aggregation) can still reach into an *already streaming* response and
     tear it down, per spec 3.3's HIGH RISK row, even though the check
     that decided HIGH ran well outside the hot path.
  5. On MEDIUM (fast-path PII), swaps in the masked text before it reaches
     the client. On HIGH, stops forwarding entirely, emits the provider's
     error/stop frame, and fires an escalation to the HITL dashboard.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from ..action_engine import Action
from ..config import settings
from ..event_bus import EventBus, new_connected_bus
from ..guardrails.fast_path import evaluate_chunk
from ..providers import UpstreamError, get_content_source
from ..providers.base import StreamChunk, WireFormat
from ..schemas import ActionDecision, EscalationEvent, Provider, RawEvent, RiskLevel, new_id
from ..util import estimate_tokens


@dataclass
class TurnState:
    trace_id: str
    provider: str
    model: str
    prompt: str
    meta: dict = field(default_factory=dict)
    accumulated: str = ""
    buffer: str = ""
    blocked: bool = False
    block_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _should_flush(buffer: str) -> bool:
    if len(buffer) >= settings.fast_path_buffer_chars:
        return True
    return bool(buffer) and buffer[-1] in settings.fast_path_flush_on


async def _publish_and_act(
    bus: Optional[EventBus],
    state: TurnState,
    text: str,
    is_final: bool,
) -> tuple[str, bool]:
    """Runs the fast path + kill-flag check on `text`, publishes the raw
    event, and returns (text_to_emit_to_client, should_stop).
    If bus is None (Redis unavailable), fast-path guardrails still run but
    telemetry is skipped silently."""

    kill_reason = (await bus.check_kill_flag(state.trace_id)) if bus else None
    fast_flag: Optional[str] = None
    text_to_emit = text

    if kill_reason:
        state.blocked = True
        state.block_reason = kill_reason
        fast_flag = "async_kill"
        text_to_emit = ""
    elif text:
        result = evaluate_chunk(text)
        if result.decision.risk_level == RiskLevel.HIGH:
            state.blocked = True
            state.block_reason = result.decision.reason
            fast_flag = "fast_path_block"
            text_to_emit = ""
            if bus:
                await bus.publish_action(ActionDecision(
                    trace_id=state.trace_id, event_id=new_id(), risk_level=result.decision.risk_level,
                    action=result.decision.action, reason=result.decision.reason,
                    contributing_agents=["responsibility_fast_path"], latency_impact=result.decision.latency_impact,
                ))
                await bus.publish_escalation(EscalationEvent(
                    trace_id=state.trace_id, risk_level=RiskLevel.HIGH, reason=result.decision.reason,
                    snippet=text[:280], prompt=state.prompt,
                ))
        elif result.decision.risk_level == RiskLevel.MEDIUM:
            fast_flag = "fast_path_edit"
            text_to_emit = result.output_text
            if bus:
                await bus.publish_action(ActionDecision(
                    trace_id=state.trace_id, event_id=new_id(), risk_level=result.decision.risk_level,
                    action=result.decision.action, reason=result.decision.reason,
                    contributing_agents=["responsibility_fast_path"], latency_impact=result.decision.latency_impact,
                ))

    state.accumulated += text_to_emit
    state.completion_tokens = estimate_tokens(state.accumulated)

    if bus:
        await bus.publish_raw_event(RawEvent(
            trace_id=state.trace_id,
            provider=Provider(state.provider),
            model=state.model,
            prompt=state.prompt,
            chunk_text=text,
            accumulated_text=state.accumulated,
            is_final=is_final or state.blocked,
            prompt_tokens=state.prompt_tokens,
            completion_tokens_so_far=state.completion_tokens,
            fast_path_flag=fast_flag,
        ))

    return text_to_emit, state.blocked



async def run_turn(
    wire: WireFormat,
    provider_name: str,
    body: dict,
    trace_id: str,
) -> AsyncIterator[str]:
    """Streaming path: yields SSE frame strings ready to write to the
    client, already in the correct provider wire format."""

    model = wire.extract_model(body)
    prompt = wire.extract_prompt(body)
    state = TurnState(trace_id=trace_id, provider=provider_name, model=model, prompt=prompt)
    state.prompt_tokens = estimate_tokens(prompt)
    state.meta = {"id": f"{provider_name}-{trace_id}", "model": model, "created": int(time.time()),
                  "prompt_tokens": state.prompt_tokens}

    try:
        bus = await new_connected_bus()
    except Exception:
        bus = None

    try:
        try:
            source = get_content_source(provider_name, model)
        except UpstreamError as e:
            yield wire.format_error_frame(str(e), state.meta)
            return

        yield wire.format_stream_start(state.meta)

        try:
            async for chunk in source.generate(prompt, model, body):
                if chunk.finished:
                    break
                if chunk.prompt_tokens:
                    state.prompt_tokens = chunk.prompt_tokens
                    state.meta["prompt_tokens"] = chunk.prompt_tokens
                state.buffer += chunk.text

                if _should_flush(state.buffer):
                    to_emit, stopped = await _publish_and_act(bus, state, state.buffer, is_final=False)
                    state.buffer = ""
                    if to_emit:
                        yield wire.format_client_chunk(StreamChunk(text=to_emit), state.meta)
                    if stopped:
                        yield wire.format_error_frame(state.block_reason, state.meta)
                        return
        except UpstreamError as e:
            yield wire.format_error_frame(f"upstream error: {e}", state.meta)
            return

        # flush whatever's left in the buffer, marking this the final event
        to_emit, stopped = await _publish_and_act(bus, state, state.buffer, is_final=True)
        state.buffer = ""
        if to_emit:
            yield wire.format_client_chunk(StreamChunk(text=to_emit), state.meta)
        if stopped:
            yield wire.format_error_frame(state.block_reason, state.meta)
            return

        state.meta["completion_tokens"] = state.completion_tokens
        yield wire.format_stream_end(state.meta)
    finally:
        if bus:
            await bus.close()


async def run_turn_buffered(wire: WireFormat, provider_name: str, body: dict, trace_id: str) -> dict:
    """Non-streaming path (client sent stream=false): still evaluates
    incrementally under the hood (so PII gets masked / toxicity still gets
    caught) but returns one JSON object at the end, per spec parity with a
    normal non-streaming LLM call."""

    model = wire.extract_model(body)
    prompt = wire.extract_prompt(body)
    state = TurnState(trace_id=trace_id, provider=provider_name, model=model, prompt=prompt)
    state.prompt_tokens = estimate_tokens(prompt)
    meta = {"id": f"{provider_name}-{trace_id}", "model": model, "created": int(time.time())}

    bus = await new_connected_bus()
    try:
        source = get_content_source(provider_name, model)
        full_text = ""
        try:
            async for chunk in source.generate(prompt, model, body):
                if chunk.finished:
                    break
                state.buffer += chunk.text
                if _should_flush(state.buffer):
                    to_emit, stopped = await _publish_and_act(bus, state, state.buffer, is_final=False)
                    state.buffer = ""
                    full_text += to_emit
                    if stopped:
                        break
        except UpstreamError as e:
            return wire.non_streaming_response(f"[ControlPlane.ai error: {e}]", {**meta, "controlplane": {"error": str(e)}})

        if not state.blocked:
            to_emit, stopped = await _publish_and_act(bus, state, state.buffer, is_final=True)
            full_text += to_emit

        meta["prompt_tokens"] = state.prompt_tokens
        meta["completion_tokens"] = state.completion_tokens
        if state.blocked:
            meta["finish_reason"] = "content_filter"
            meta["controlplane"] = {"blocked": True, "reason": state.block_reason, "trace_id": trace_id}
            full_text = full_text + f"\n\n[ControlPlane.ai blocked the remainder of this response: {state.block_reason}]"
        else:
            meta["controlplane"] = {"blocked": False, "trace_id": trace_id}
        return wire.non_streaming_response(full_text, meta)
    finally:
        await bus.close()
