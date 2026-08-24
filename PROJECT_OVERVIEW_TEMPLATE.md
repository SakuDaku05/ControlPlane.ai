# ControlPlane.ai — Project Overview

*A fill-in-and-share template for teammates, mentors, or judges. Sections marked `[FILL IN]` need your input — everything else is already written from the current build.*

---

## 1. One-liner

> ControlPlane.ai is a non-blocking sidecar proxy that gives enterprises real-time oversight of their LLM traffic — catching hallucinations, cost overruns, and PII/toxicity leaks *before* they reach the end user, without adding perceptible latency.

**Team:** `[FILL IN — team name / number, e.g. 23je0841]`
**Event:** `[FILL IN — e.g. Accenture Innovation Challenge 2026]`
**Date:** `[FILL IN]`

---

## 2. The problem

Enterprises deploying LLMs have no real-time telemetry across inference — they find out something went wrong (a hallucinated answer, a leaked SSN, a runaway bill) only *after* a user complains. We call this **"After the Fact Discovery."** Existing tooling is either purely reactive (logging/analytics after the response is already out) or adds so much latency that it breaks the product experience.

## 3. The solution

A **sidecar proxy** sits between the application and the LLM provider (OpenAI/Anthropic/etc.), intercepting every request and response. It:

1. Forwards the stream to the user **immediately** — no added latency on the happy path.
2. **Forks** a copy of the stream onto an async event bus.
3. Evaluates that fork in parallel across three dimensions — **Performance** (hallucination/grounding), **Cost** (token burn), **Responsibility** (PII/toxicity/data leakage) — using independent micro-agents.
4. A **Dynamic Action Engine** turns those findings into one of three actions, in real time:

| Risk tier | Action | What happens |
|---|---|---|
| LOW | **Pass-Through** | Stream proceeds untouched, <50ms overhead |
| MEDIUM | **Auto-Edit** | PII/secrets masked inline before reaching the client |
| HIGH | **Block & Escalate** | Connection torn down mid-stream, human-in-the-loop dashboard alerted |

---

## 4. Architecture

```
Application → [ControlPlane.ai Sidecar Proxy] → LLM Provider (OpenAI/Anthropic)
                     │            │
           forwards immediately   forks to event bus (Redis Streams)
                     │            │
                  Client    ┌─────┴─────┬──────────────┐
                             ▼           ▼              ▼
                       Performance    Cost         Responsibility
                         Agent        Agent            Agent
                       (grounding/  (token/$      (PII/toxicity,
                       hallucination)  burn)       deep re-scan)
                             │           │              │
                             └─────┬─────┴──────────────┘
                                   ▼
                          Dynamic Action Engine
                           (risk tier → action)
                                   ▼
                          HITL Dashboard (live)
```

**Why a fork instead of a blocking check:** the expensive checks (semantic grounding, cumulative cost) can't run fast enough to sit in the critical path without hurting UX — so they run asynchronously and reach back into an *already-streaming* response through a lightweight kill-switch if something needs to stop it. Cheap, deterministic checks (regex PII, keyword toxicity) run inline because they're fast enough not to matter.

---

## 5. Code structure

```
controlplane/
  config.py, schemas.py          # settings + typed events shared everywhere
  redis_client.py, event_bus.py  # the async event bus (Redis Streams)
  action_engine.py               # risk tiering → pass/edit/block
  guardrails/                    # fast regex PII + toxicity checks (hot path)
  kb/                            # sample enterprise knowledge base + grounding retriever
  providers/                     # OpenAI / Anthropic / offline-demo adapters
  proxy/                         # the sidecar itself (intercept, fork, stream)
  agents/                        # Performance / Cost / Responsibility micro-agents
  dashboard/                     # live HITL dashboard (SSE)
  run_all.py, run_agent.py       # one-process (demo) and one-per-process (prod) launchers
demo/                            # canned scenarios + CLI client for a live walkthrough
docker-compose.yml               # 5-service real deployment topology
```

## 6. Tech stack

| Layer | Technology |
|---|---|
| Proxy / dashboard web framework | Starlette + Uvicorn |
| Streaming to dashboard | Server-Sent Events (sse-starlette) |
| Event bus | Redis Streams (consumer groups per agent) |
| Upstream LLM APIs | OpenAI `/v1/chat/completions`, Anthropic `/v1/messages` — provider-agnostic adapter |
| Hallucination/grounding | TF-IDF cosine similarity + numeric-claim consistency check over an enterprise KB |
| PII detection | Regex + Luhn-validated pattern matching (emails, SSNs, cards, API keys, JWTs) |
| Toxicity detection | Weighted lexical/pattern scorer |
| Deployment | Docker Compose (5 independently-scalable services) |

## 7. What's built and working today

- End-to-end sidecar proxy with real streaming pass-through, inline masking, and mid-stream connection teardown
- All three micro-agents running as independent async consumers off the event bus
- Dynamic Action Engine implementing the full LOW/MEDIUM/HIGH mitigation table
- Live HITL dashboard showing real-time actions, agent findings, and escalations
- 8 deterministic demo scenarios covering every risk tier, runnable with zero API keys via an offline mock provider
- Docker Compose topology for a real multi-service deployment

## 8. Honest limitations / what we'd add next

`[FILL IN — tailor to audience, e.g.:]`
- Swap the lexical hallucination/toxicity detectors for fine-tuned neural models (cross-encoder, moderation API) once given model-download access
- Connect the knowledge base to a real vector store instead of the sample corpus
- Add real per-model tokenizers for exact billing-grade cost tracking
- Add authentication/multi-tenancy for production use

## 9. Demo script (suggested talking points)

`[FILL IN — pick 2-3 to show live]`
1. **LOW risk** — ask a grounded question, show it pass straight through with a clean dashboard entry.
2. **MEDIUM risk (PII)** — show a response get PII masked inline, live, mid-stream.
3. **HIGH risk (toxicity or hallucination)** — show the connection get torn down and an escalation appear instantly on the dashboard.

## 10. Team & contact

`[FILL IN]`
- Name / role:
- Name / role:
- Repo / contact:
