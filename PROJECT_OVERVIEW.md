# ControlPlane.ai

A working implementation of the ControlPlane.ai PRD & Technical Spec: a non-blocking sidecar proxy that intercepts LLM traffic, forks it to an asynchronous event bus, evaluates it in parallel across three risk dimensions (Performance / Cost / Responsibility), and automatically passes, edits, or blocks the output — with a live human-in-the-loop dashboard.

```
                     ┌─────────────────────────────────────────────┐
                     │              Your Application                │
                     └───────────────────┬───────────────────────────┘
                                          │ (OpenAI/Anthropic-shaped request)
                                          ▼
                     ┌─────────────────────────────────────────────┐
                     │        ControlPlane.ai Sidecar Proxy          │
                     │  • synchronous fast-path (regex PII/toxicity) │
                     │  • forwards stream to client immediately      │
                     │  • forks every chunk onto the event bus        │
                     └──────┬───────────────────────────┬────────────┘
                            │ passthrough/edited stream   │ XADD (fork)
                            ▼                             ▼
                     ┌───────────┐            ┌─────────────────────────┐
                     │  Client   │            │   Redis Streams event bus │
                     └───────────┘            └───┬─────────┬─────────┬──┘
                                                    │         │         │
                                        ┌───────────▼─┐ ┌────▼────┐ ┌──▼────────────┐
                                        │ Performance │ │  Cost   │ │ Responsibility│
                                        │   Agent     │ │  Agent  │ │     Agent      │
                                        │ (grounding/ │ │ (token/ │ │ (PII/toxicity  │
                                        │hallucination)│ │$ burn) │ │  deep re-scan) │
                                        └──────┬──────┘ └────┬────┘ └───────┬───────┘
                                               │              │              │
                                               └──────┬───────┴──────┬───────┘
                                                       ▼              ▼
                                            ┌─────────────────┐  kill-flag (Redis key)
                                            │ Dynamic Action   │  → can still tear down
                                            │     Engine       │    an in-flight stream
                                            └────────┬─────────┘
                                                      ▼
                                            ┌─────────────────┐
                                            │  HITL Dashboard  │  (live SSE feed)
                                            └─────────────────┘
```

## Quickstart (no Docker — what this was built and tested against)

```bash
pip install -r requirements.txt
redis-server --daemonize yes          # or: docker run -p 6379:6379 redis:7-alpine

python -m controlplane.run_all
```

This starts the sidecar proxy on `:8080`, the dashboard on `:8090`, and all three micro-agents, in one process. Open `http://localhost:8090` for the dashboard, then in another terminal:

```bash
python -m demo.demo_client --list
python -m demo.demo_client --scenario high_toxicity
python -m demo.demo_client --scenario high_pii_multi --provider anthropic
python -m demo.demo_client --all        # run every scenario back to back
```

No API keys are required for the demo — every scenario runs against the built-in offline `mock:*` provider. To point it at a real model instead, set `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` and send a request with a real model name (e.g. `gpt-4o-mini`, `claude-sonnet-4-5`) to `/v1/chat/completions` or `/v1/messages`.

## Dependencies

- Python 3.11+
- Docker & Docker Compose
- OpenAI or Anthropic API key, or use the mock provider

## Getting started

1. Clone the repository.
2. Provide your API keys and configuration in the `.env` file.
3. Build and launch the cluster:

```bash
docker-compose up --build
```

4. Open the dashboard at `http://localhost:8090`.
5. The proxy is listening on `http://localhost:8080/v1/chat/completions`.

## Running tests

```bash
pip install pytest pytest-asyncio
export PYTHONPATH="."
pytest tests/
```

## Mapping to the spec

| Spec section | Implementation |
|---|---|
| 2.2 Feature 1 — Non-Blocking Sidecar Interception | `controlplane/proxy/` |
| 2.2 Feature 2 — Multi-Dimensional Micro-Agent Evaluation | `controlplane/agents/{performance,cost,responsibility}_agent.py` |
| 2.2 Feature 3 — Dynamic Action Engine | `controlplane/action_engine.py` |
| 3.2.1 Network Proxy Layer (Sidecar) | `controlplane/proxy/app.py` |
| 3.2.2 Asynchronous Event Bus | `controlplane/event_bus.py` + `controlplane/redis_client.py` |
| 3.2.3 Evaluation Micro-Agents | `controlplane/agents/`, `controlplane/kb/`, `controlplane/guardrails/` |
| 3.3 Mitigation Protocol (LOW/MEDIUM/HIGH → Pass/Edit/Block) | `controlplane/action_engine.py::decide()` |
| HITL dashboard / webhook | `controlplane/dashboard/` |

## Design decisions

This project resolves a few spec tensions in a practical way:

- Fast, deterministic checks run in the hot path and expensive semantic checks run asynchronously.
- The proxy uses a Redis kill-flag to stop an in-flight stream when a high-risk event is detected.
- The event bus uses Redis Streams so every agent can observe the same turn without losing at-least-once delivery.
- The system supports both mock and real provider traffic.

## Demo scenarios

The project includes deterministic scenarios such as:

| Scenario id | Risk tier | Purpose |
|---|---|---|
| `low_grounded_refund` | LOW | Safe answer passes through |
| `medium_pii_leak` | MEDIUM | Auto-edit/redact PII mid-stream |
| `high_toxicity` | HIGH | Toxic request is blocked and escalated |
| `high_hallucination_sla` | HIGH | Grounding failure triggers escalation |
| `cost_burn` | HIGH | Budget exhaust triggers block |

## Project layout

```text
controlplane/
  config.py
  schemas.py
  redis_client.py
  event_bus.py
  action_engine.py
  util.py
  guardrails/
  kb/
  providers/
  proxy/
  agents/
  dashboard/
  run_all.py
  run_agent.py
demo/
  scenarios.py
  demo_client.py
docker-compose.yml
Dockerfile
requirements.txt
PROJECT_OVERVIEW.md
```

## Known limitations

- Lexical and TF-IDF-based guardrails are intentionally explainable but not as strong as a full neural model.
- The knowledge base is a sample enterprise corpus rather than a production vector store.
- Cost estimation uses heuristics unless a provider reports exact usage.
