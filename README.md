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

No API keys are required for the demo — every scenario runs against the built-in offline `mock:*` provider (see [Design Decisions](#design-decisions)). To point it at a real model instead, set `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` (see `.env.example`) and send a request with a real model name (e.g. `gpt-4o-mini`, `claude-sonnet-4-5`) to `/v1/chat/completions` or `/v1/messages`### Dependencies
* Python 3.11+
* Docker & Docker Compose
* An OpenAI or Anthropic API key (or use the mock provider)

### Getting Started

1. Clone the repository
2. Provide your API keys and configuration in the `.env` file (see `.env.example`).
   > **Note:** A PAT token (`PAT_TOKEN`) is provided in the `.env` file to push changes back to the repository easily.
3. Build and launch the cluster:
   ```bash
   docker-compose up --build
   ```
4. Access the **ControlPlane Dashboard** at `http://localhost:8090`
5. The proxy is now listening on `http://localhost:8080/v1/chat/completions`

### Running Tests

We use `pytest` for the unit tests. Make sure you install the development dependencies and set your `PYTHONPATH`:

```bash
pip install pytest pytest-asyncio
$env:PYTHONPATH="."  # On Windows PowerShell
export PYTHONPATH="." # On Mac/Linux
pytest tests/
```tainers**, each independently scalable — this is the topology the spec's "scalable interception layer" language calls for. `run_all.py` and `docker-compose.yml` share 100% of the same application code; they're just two different process topologies around it.

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

The PRD is aspirational in a few places where a literal reading would be internally inconsistent or would require infrastructure this build environment (or arguably any environment without a very specific stack) can't provide out of the box. Rather than pretend, here's what was decided and why:

**"Zero perceived latency" vs. "the proxy tears down the connection for HIGH risk."** These are in tension: you cannot intercept and stop a stream you never look at. The resolution used here — and the one real guardrail products (WAF-layer DLP, LLM gateways) converge on — is two speeds: cheap, deterministic regex/lexical checks run **synchronously**, in the hot path, on small buffered chunks (default 80 characters or one clause), which is fast enough to stay well under the spec's own "<50ms TTFT" budget. The expensive semantic checks (grounding/hallucination similarity, cumulative cost aggregation) run **asynchronously** in the background agents and reach back into a still-open stream via a tiny Redis "kill flag" the proxy polls between chunk flushes — see `event_bus.raise_kill_flag` / `check_kill_flag` and the docstring at the top of `proxy/streaming.py`.

**No cross-encoder / neural NER / real Redis or WebSocket client.** This was built in a sandboxed environment with **zero outbound network access to PyPI, npm, or the Ubuntu package mirrors** (verified directly — all return HTTP 403 at the egress layer). That ruled out `pip install redis`, `fastapi`, `websockets`, and any Hugging Face model download. Rather than fake these with something that only looks like the real architecture:
- `controlplane/redis_client.py` is a from-scratch async RESP2 client speaking real wire protocol to a real `redis-server`. Swap it for `redis.asyncio.Redis` in one file (`event_bus.py`) the moment you're in an environment with normal internet access — method names were chosen to match 1:1.
- The dashboard uses Server-Sent Events (`sse-starlette`, already available) instead of WebSockets, since no `websockets`/`wsproto` package was installable. SSE is one-directional, which is all a live telemetry feed needs.
- The Performance Agent's grounding check uses TF-IDF cosine similarity + an explicit numeric-claim consistency check (`controlplane/kb/retriever.py`) instead of a downloaded sentence-transformers cross-encoder. It's calibrated against a real test suite of grounded/paraphrased/fabricated examples (see the module docstring) and reliably catches the highest-stakes hallucination shape — "right topic, invented number" — but is genuinely weaker than a neural model at rewarding heavy paraphrase. The seam to upgrade is one function: `Retriever.grounding_score()`.
- The Responsibility Agent's toxicity scorer is an explainable weighted lexical/pattern scorer (`controlplane/guardrails/toxicity.py`), not a fine-tuned classifier — again, no model download available. Every score traces back to specific matched terms, which is a legitimately nice property for an auditable enterprise guardrail even setting the constraint aside.

**Defense in depth against chunk-boundary evasion.** A structured secret (an SSN, a card number) can straddle two streamed chunks and be invisible to any single-chunk regex scan. The synchronous fast path only ever sees one buffer at a time; the Responsibility Agent re-scans the *full accumulated text* on every event asynchronously, so anything that slips past the hot path chunk-by-chunk still gets caught once the complete picture is visible (see the docstring in `agents/responsibility_agent.py`).

**Redis Streams (not plain Pub/Sub) for the event bus.** The spec says "Redis/Kafka" and "pub/sub messaging." Plain Pub/Sub is fire-and-forget: a message published while an agent is mid-restart is lost forever, which is unacceptable for a PII/data-leakage detector. Streams + one consumer group per agent gives real fan-out (every agent independently sees every event) with at-least-once delivery (`XACK`) and a free audit log (`XRANGE` over the actions stream) — the modern, still-Redis-native answer to "pub/sub messaging" that a Kafka migration later would only touch in `event_bus.py`.

## Demo scenarios

Every scenario is fully deterministic and requires no API keys — see `controlplane/providers/mock_provider.py` for the full text of each, and `demo/scenarios.py` for what to expect on the dashboard.

| Scenario id | Tier | What it demonstrates |
|---|---|---|
| `low_grounded_refund`, `low_grounded_sla` | LOW | Clean, on-topic, accurate answer — streams through untouched |
| `medium_pii_leak` | MEDIUM | Email/phone masked inline mid-stream |
| `medium_toxicity` | MEDIUM | Mild rudeness flagged |
| `medium_hallucination` | MEDIUM | Plausible but ungrounded embellishment |
| `high_toxicity` | HIGH | Violent threat — connection torn down mid-sentence |
| `high_hallucination_sla` | HIGH | Massive fabricated guarantee — caught by the async Performance Agent after the fact, kill-flag fires |
| `high_pii_multi` | HIGH | Multiple high-confidence secrets in one turn — immediate fast-path block |
| `cost_burn` | HIGH (cost) | Runaway generation cut off mid-stream once session budget is exceeded |

## Project layout

```
controlplane/
  config.py            settings (env-driven)
  schemas.py            typed events shared across every component
  redis_client.py        dependency-free async RESP2 client
  event_bus.py            Streams-based async event bus (spec 3.2.2)
  action_engine.py         Dynamic Action Engine / risk tiering (spec 3.3)
  util.py                   token estimation helper
  guardrails/
    pii.py                    fast regex PII/secret detection
    toxicity.py                 lexical toxicity scorer
    fast_path.py                  synchronous hot-path combining both
  kb/
    documents.py                   sample enterprise knowledge base
    retriever.py                     TF-IDF grounding/hallucination scorer
  providers/
    base.py                            WireFormat / ContentSource interfaces
    openai_provider.py                    OpenAI-shaped wire format
    anthropic_provider.py                   Anthropic-shaped wire format
    mock_provider.py                          offline scripted demo provider
  proxy/
    app.py                                       Starlette routes (spec 3.2.1)
    streaming.py                                    fork/forward/mask/block logic
  agents/
    base_agent.py                                       shared consumer loop
    performance_agent.py                                   hallucination detection
    cost_agent.py                                            token/cost telemetry
    responsibility_agent.py                                    PII/toxicity deep scan
  dashboard/
    app.py                                                       SSE HITL dashboard
    static/index.html                                              dashboard UI
  run_all.py            one-process launcher (dev/demo)
  run_agent.py            one-agent-per-process launcher (docker-compose)
demo/
  scenarios.py          canned scenario metadata
  demo_client.py           CLI that drives the proxy and prints the raw output
docker-compose.yml     five-service real deployment topology
Dockerfile
requirements.txt
.env.example
```

## Known limitations / honest upgrade path

- **Grounding/toxicity are lexical, not neural**, for the network-access reasons above. Swap `Retriever.grounding_score()` for a `sentence-transformers` `CrossEncoder`, and `guardrails/toxicity.score_toxicity()` for a fine-tuned classifier or moderation API call, the moment you're in an environment that can download them. Nothing else in the codebase needs to change — both are called through a narrow, already-abstracted interface.
- **The knowledge base is a small fictional corpus** (`kb/documents.py`) standing in for a real enterprise's ingested docs. Point `Retriever` at a real vector store for production use.
- **Single-process Redis client**: `redis_client.RedisClient` handles one in-flight command at a time per connection (each request/agent opens its own connection, so concurrency still scales with connection count, not command pipelining). The real `redis` package's connection pooling is a drop-in upgrade.
- **Cost figures are estimated** via a word-count heuristic (`util.estimate_tokens`) except where a real provider reports usage; swap in `tiktoken` or the model's real tokenizer for exact billing-grade numbers.
