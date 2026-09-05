# ControlPlane.ai — Interview Preparation Guide
### Accenture Innovation Challenge — AI-Led Technical Discussion

---

## Part 1: Project Overview & System Design

### What is ControlPlane.ai?

ControlPlane.ai is a **non-blocking sidecar proxy** that sits between any application and an upstream LLM provider (OpenAI, Anthropic, or custom models). It intercepts all outbound LLM traffic in real time, evaluates it across three risk dimensions simultaneously, and enforces an automated risk policy — all without blocking the primary user-facing stream.

The core thesis: **you should not trust LLM output by default.** Every response needs to be audited for safety, accuracy, and cost before it reaches an end user at enterprise scale.

---

### High-Level Architecture

```
Your Application
       │
       ▼  (OpenAI/Anthropic-shaped HTTP request)
┌──────────────────────────────────────┐
│     ControlPlane.ai Sidecar Proxy    │
│  • Synchronous fast-path guardrails  │
│  • Forwards stream to client         │
│  • Forks every chunk to event bus    │
└───────────┬──────────────────────────┘
            │  XADD (async fork, sub-ms)
            ▼
    ┌─────────────────────┐
    │  Redis Streams Bus  │
    └──┬──────┬───────┬───┘
       │      │       │
  ┌────▼──┐ ┌─▼────┐ ┌▼──────────────┐
  │ Perf  │ │ Cost │ │ Responsibility │
  │ Agent │ │Agent │ │    Agent       │
  └────┬──┘ └─┬────┘ └───────┬───────┘
       │      │               │
       └──────┴───────────────┘
                    │
                    ▼
         Dynamic Action Engine
                    │
          ┌─────────┼──────────┐
        PASS       EDIT      BLOCK
                              │
                              ▼
                     HITL Dashboard
                   (live SSE feed)
```

---

### The Three Layers Explained

#### Layer 1 — Synchronous Fast-Path (Hot Path)

Runs **inline** inside the proxy's streaming loop. Operates on buffered chunks (≤80 chars or sentence boundaries). Only regex-speed checks:

- **PII detection**: Compiled regexes for emails, SSNs, credit cards, API keys (OpenAI `sk-...`, Anthropic `sk-ant-...`, AWS `AKIA...`), JWTs, phone numbers, IPv4 addresses. Credit cards validated with Luhn check.
- **Toxicity scoring**: Weighted lexical scorer — severe patterns (violence/threats) score ≥0.85, moderate insults score 0.35–0.83, mild profanity score 0.15+.

Why only this? The spec demands `<50ms TTFT overhead` — running neural models inline would blow this budget.

#### Layer 2 — Asynchronous Micro-Agents (Deep Analysis)

Three independent workers consume from Redis Streams:

| Agent | What it checks | Key technique |
|---|---|---|
| **Performance Agent** | Hallucination / grounding | ChromaDB vector store + sentence-transformers, numeric-claim consistency check |
| **Cost Agent** | Token budget burn | Per-trace Redis hash counter, heuristic token estimation, per-model pricing table |
| **Responsibility Agent** | Full accumulated PII + toxicity | Re-scans entire accumulated turn text (catches chunk-boundary-split secrets) |

Each agent independently reads from the same stream (via separate consumer groups), evaluates with the Action Engine, and either publishes a finding or raises a **kill flag**.

#### Layer 3 — Dynamic Action Engine + HITL Dashboard

A single `decide()` function is the sole authority on risk classification. It maps risk inputs to:

| Risk Level | Action | What happens |
|---|---|---|
| **LOW** | `PASS_THROUGH` | Stream proceeds uninterrupted |
| **MEDIUM** | `AUTO_EDIT` | Inline PII masking before flush to client |
| **HIGH** | `BLOCK_AND_ESCALATE` | Connection torn down; kill flag raised; HITL dashboard alerted |

---

### The Kill-Flag Mechanism (Key Design Decision)

This solves a subtle but critical problem: **async agents can detect HIGH risk after the stream has already started**.

Solution: A tiny TTL'd Redis key `cp:kill:{trace_id}`. The proxy checks this key between every buffer flush. If an async agent (e.g. Performance Agent detecting hallucination after seeing 200+ characters) sets the kill flag, the proxy tears down the **still-open** stream mid-flight. The user sees the response cut off — but the harmful content never completes delivery.

---

### The Event Bus (Redis Streams vs. Pub/Sub)

Redis Streams were chosen over plain Pub/Sub deliberately:

- **Fan-out**: Each agent has its own consumer group → all three see every event independently.
- **At-least-once delivery**: `XACK` means a crashed agent's unprocessed messages are reclaimable, not silently dropped. Critical for a safety/PII agent.
- **Natural audit log**: `XRANGE` over `cp:events:actions` gives a full compliance trail.
- **Swap-friendly**: The `EventBus` interface is narrow enough that replacing Redis Streams with Kafka only touches `event_bus.py`.

Four Redis streams in use:
- `cp:events:raw` — proxy → agents (one per buffer flush)
- `cp:events:results` — agents → action engine / dashboard
- `cp:events:actions` — action engine decisions (audit trail)
- `cp:events:escalations` — HIGH risk → HITL dashboard

---

### Grounding & Hallucination Detection

The Performance Agent uses **ChromaDB** with `sentence-transformers/all-MiniLM-L6-v2` embeddings to compute cosine similarity between the LLM's output and an enterprise knowledge base.

Two-signal scoring:
1. **Semantic similarity** from ChromaDB (L2 → cosine conversion)
2. **Numeric-claim consistency**: Extracts all dollar figures, percentages, and durations from both the LLM output and the best-matched KB document. If the LLM claims "$100M revenue" but the KB says "$47M", similarity is penalized down.

Thresholds (configurable via env vars):
- Similarity < 0.10 → HIGH (massive hallucination)
- Similarity 0.10–0.30 → MEDIUM (weak grounding)
- Similarity > 0.30 → LOW (well grounded)

---

### Deployment Model

**Single process (local dev)**:
```bash
python -m controlplane.run_all
```
Starts proxy (`:8080`), dashboard (`:8090`), and all three agents as co-located async tasks.

**Multi-container (production)**:
```bash
docker-compose up --build
```
Each component runs in its own container. Zero code changes needed — they communicate exclusively through Redis.

This is the 12-factor design: env-driven config, stateless services, external backing store (Redis).

---

### Provider Compatibility

The proxy is provider-agnostic. It speaks two wire formats:
- **OpenAI-style**: `POST /v1/chat/completions`
- **Anthropic-style**: `POST /v1/messages`

A `mock:` provider is built in for offline demos — no API keys required.

---

## Part 2: Q&A — Expected Interview Questions

---

### Category 1: Understanding the Problem Statement

---

**Q1. What problem does ControlPlane.ai solve?**

Enterprise AI deployments face three risks that current LLM providers don't solve:
1. **Safety/Responsibility** — models can emit toxic content, PII, or policy violations.
2. **Accuracy/Performance** — models hallucinate, especially on enterprise-specific facts.
3. **Cost/Compute** — runaway generation loops or malicious prompt injection can exhaust budgets.

Existing solutions either block the stream entirely (bad UX) or evaluate post-hoc (too late — content already reached the user). ControlPlane.ai evaluates in real time while preserving user-perceived latency by splitting cheap checks into the hot path and expensive checks into async workers that can still stop an in-flight stream.

---

**Q2. What problem statement did you choose, and why?**

We chose the **AI Governance / LLM Safety** problem space. The core insight was that most enterprise LLM deployments have no systematic layer between the model and the end user — they rely on prompt engineering alone. This is fragile: a single adversarial prompt or model drift event can expose PII or serve dangerous content to hundreds of users before anyone notices. A network-level sidecar proxy is the right architectural answer because it is transparent to the application — no SDK changes, no code changes — and it can enforce policy uniformly across all LLM calls regardless of which team or feature makes them.

---

**Q3. Who are the target users / what is the use case?**

Enterprise organizations deploying LLMs internally (HR bots, customer service, code assistants, document summarizers). The pain points are:
- Legal/compliance risk from PII leakage (GDPR, HIPAA)
- Reputational risk from toxic or biased outputs
- Financial risk from unexpected API cost spikes
- Accuracy risk from hallucinations presented as facts to non-technical end users

---

### Category 2: Technical Depth

---

**Q4. Walk me through what happens when a request hits the proxy.**

1. The application sends an OpenAI- or Anthropic-shaped HTTP request to the proxy on `:8080`.
2. The proxy identifies the provider/model and creates a `TurnState` with a unique `trace_id`.
3. It opens a streaming connection to the upstream model (real or mock).
4. As chunks arrive, they accumulate in a small buffer (≤80 chars or up to the next sentence boundary).
5. When the buffer flushes:
   - **Kill-flag check**: Is there a `cp:kill:{trace_id}` key in Redis? If yes → stop streaming, return error frame.
   - **Fast-path guardrails**: Run `evaluate_chunk()` — PII detection + toxicity scoring. HIGH → block. MEDIUM → mask PII inline. LOW → pass through.
   - **Publish to event bus**: `XADD` a `RawEvent` to `cp:events:raw` with the chunk text, accumulated text, and metadata.
6. Meanwhile, the three async agents consume events from the bus and evaluate independently.
7. If any agent hits HIGH, it raises the kill flag → the proxy's next buffer flush catches it and tears down the stream.
8. All decisions are published to `cp:events:actions` and escalations to `cp:events:escalations`.
9. The HITL dashboard consumes both streams via SSE and shows them to human reviewers.

---

**Q5. Why did you use Redis Streams instead of a message queue like RabbitMQ or Kafka?**

Redis was already the obvious choice for the kill-flag (a single TTL'd key). Adding Kafka just for the event bus would mean operating a second infrastructure dependency in a demo environment. Redis Streams are Redis's answer to a Kafka-style partitioned log: consumer groups, at-least-once delivery via `XACK`, fan-out to multiple consumers, and a natural audit trail. The `EventBus` class wraps everything in a narrow interface, so swapping the transport to real Kafka (e.g. `aiokafka`) in production only touches `event_bus.py`.

---

**Q6. How does the kill-flag work, and why not use a second stream for it?**

A kill flag is a single bit: "this trace is done, stop it." A TTL'd Redis key (`SET cp:kill:{trace_id} reason EX 120`) is:
- **Sub-millisecond**: `GET` of a simple key is the fastest Redis operation.
- **Cheap**: One `GET` per buffer flush in the hot path.
- **Self-cleaning**: TTL of 120 seconds means no manual cleanup.
- **Cross-process**: Works whether proxy and agents are co-located or in separate containers.

The proxy polls it between every buffer flush. If the flag is present, the stream tears down immediately, even mid-sentence.

---

**Q7. How does PII detection work? Why not use spaCy or a transformer NER model?**

Two reasons:
1. **Latency budget**: The fast path has a `<50ms TTFT overhead` budget. Transformer inference takes 50–200ms on CPU.
2. **Structured PII is regex-solvable**: SSNs, credit cards, API keys, emails have precise, deterministic formats. Regex with structural validation (Luhn check for cards, area-code plausibility for phones) is sub-millisecond.

The limitation (unstructured NER — person names, org names) is acknowledged and slots into the async Responsibility Agent, where the latency budget is unlimited. The architecture explicitly separates "what's fast enough to run inline" from "what runs async."

---

**Q8. How does hallucination detection work?**

The Performance Agent uses ChromaDB with `sentence-transformers/all-MiniLM-L6-v2` embeddings against an enterprise KB. When the accumulated response is long enough (>40 chars) or the turn is final:

1. Queries ChromaDB for the top-1 most similar KB document.
2. Converts L2 distance to cosine similarity.
3. Extracts numeric claims (dollar amounts, percentages, durations) from both the LLM output and the matched KB document.
4. If the LLM makes numeric claims not in the KB, the similarity score is penalized proportionally.
5. The final score feeds into `decide()` → LOW/MEDIUM/HIGH risk.

This catches two classes: topically off-base responses (low semantic similarity) and factually wrong-but-on-topic responses (correct topic, invented numbers).

---

**Q9. How does cost tracking work?**

The Cost Agent uses a per-trace Redis hash (`cp:cost:{trace_id}`) as a running counter. On each event:
1. Estimates tokens from chunk text (`words / 0.75` heuristic).
2. Looks up the model in a pricing table (GPT-4, Claude, Gemini, etc.).
3. Calls `HINCRBYFLOAT` to accumulate cost atomically in Redis.
4. Computes a burn ratio against the session budget (`SESSION_BUDGET_USD`, default $0.02).
5. `burn_ratio >= 1.0` → HIGH risk → kill flag → stream terminated.
6. `burn_ratio >= 0.75` → MEDIUM risk → warning logged.

---

**Q10. How does the proxy handle both streaming and non-streaming requests?**

Two code paths in `streaming.py`:
- `run_turn()`: Streaming path. Yields SSE frames. Fast path + kill-flag check runs between every buffer flush.
- `run_turn_buffered()`: Non-streaming path (`stream=false`). Still evaluates incrementally internally (PII still caught), but returns one JSON object. Blocked responses include a `controlplane` metadata field explaining the block.

---

### Category 3: Methodology & Approach

---

**Q11. What was your team's approach and methodology?**

We started from the spec's core tension: enforce a strict safety policy (`HIGH risk → connection terminated`) while promising `<50ms TTFT overhead`. These are contradictory if you run everything serially.

Resolution: **tiered evaluation**.
- Tier 1 (hot path): Only regex-speed checks — fast enough to run inline and can block synchronously.
- Tier 2 (async): Everything computationally expensive — embedding inference, cumulative cost, full-text re-scan. Still stops a stream via the kill-flag.

We built bottom-up: schemas and event bus first (so each component has a clear contract), then the proxy, then the agents, then the dashboard. Docker Compose was written early to validate the multi-process model matched the single-process dev model.

---

**Q12. What key design decisions did you make and why?**

1. **Sidecar proxy over SDK instrumentation**: A proxy requires zero application code changes. Enterprise compliance requires uniform coverage across all teams.
2. **Single `decide()` function**: One authoritative risk decision function prevents drift between hot path and async agents. Both call the same `decide(RiskInputs)`.
3. **Redis Streams over Pub/Sub**: At-least-once delivery is non-negotiable for a safety agent. A dropped Pub/Sub message containing PII is a compliance failure.
4. **Explicit risk tiers (not just a score)**: A continuous 0–1 score is hard to operationalize. Three tiers (LOW/MEDIUM/HIGH) with clear actions are auditable and explainable to compliance teams.
5. **Explainable guardrails over black-box models**: Lexical toxicity scoring traces every verdict to specific matched terms. A HITL reviewer can see *why* something was flagged — more useful for enterprise compliance than a neural model outputting `[0.93]`.

---

### Category 4: Feasibility, Scalability & Real-World Applicability

---

**Q13. Is this actually deployable at enterprise scale?**

Yes:
- **Zero application changes**: Just change the base URL from `api.openai.com` to the proxy address.
- **Horizontal scalability**: Each component is a stateless process talking to Redis. Scale by adding instances.
- **Provider-agnostic**: OpenAI, Anthropic, and any OpenAI-compatible endpoint.
- **Configurable thresholds**: All risk thresholds and budgets are env-var driven — no redeployment for tuning.
- **Audit trail**: Every decision written to `cp:events:actions` — a natural compliance log.

---

**Q14. What is the latency overhead?**

For a LOW-risk (passing) response per buffer flush:
- **Fast-path guardrails**: Regex on 80-char buffer — sub-millisecond.
- **Redis XADD**: ~0.5–1ms local, ~2–5ms cloud Redis.
- **Kill-flag GET**: ~0.2ms.

Total: **< 5ms** per flush, well within the `<50ms TTFT` budget.

---

**Q15. How does it scale to thousands of concurrent LLM requests?**

- **Proxy**: Each request is an independent async task. No shared mutable state per request. Multiple instances behind a load balancer.
- **Agents**: Independent asyncio loops consuming from consumer groups. Scale each agent independently.
- **Redis**: Single instance handles millions of ops/second. Redis Cluster for extreme scale.
- **Dashboard**: SSE is lightweight — one instance fans out to many tabs.

---

**Q16. What are the known limitations?**

1. **Lexical toxicity vs. neural classifier**: Misses subtle context-dependent toxicity (sarcasm, implied threats). Production upgrade: fine-tuned BERT classifier or Perspective API behind the same interface.
2. **PII only catches structured formats**: Unstructured PII (names in free text) requires ML NER. Planned for the async Responsibility Agent.
3. **Knowledge base is a sample corpus**: Real deployment requires populating ChromaDB with the org's actual policy docs, product data, SOPs.
4. **In-memory ChromaDB**: `chromadb.Client()` is in-process, ephemeral. Production: persistent ChromaDB server or managed vector DB (Pinecone, Weaviate).
5. **Token estimation is heuristic**: `words / 0.75` is approximate. Exact counts require `tiktoken` per-chunk (installed, not used inline to save overhead).

---

### Category 5: Challenges & How We Addressed Them

---

**Q17. What was the hardest technical challenge?**

The kill-flag mechanism. The challenge: **how do you stop a stream already in flight when the stop decision is made asynchronously by a different process?**

Options we considered and rejected:
- **WebSocket signaling**: Requires a persistent agent-proxy connection — too complex.
- **Shared in-process state**: Breaks in Docker multi-container mode.
- **Another Redis stream**: Overkill for one bit; introduces ordering complexity.

Solution: A tiny TTL'd Redis key. The proxy polls it between every flush. Simple, cross-process, self-cleaning, sub-millisecond. The insight: "stop streaming" is fundamentally a different shape of message than "here is evaluation data" — it needs lowest-latency delivery, not a queue.

---

**Q18. How did you handle the tension between "zero latency" and "catch everything"?**

This is the fundamental design tension of the whole project. The PRD asks for both simultaneously — physically impossible if interpreted literally.

Resolution: **tiered evaluation + kill flag**.
- Zero latency applies to LOW-risk traffic. An 80-char buffer flush with regex + Redis GET takes ~2ms — imperceptible.
- HIGH-risk content on the fast path (obvious toxicity) blocks synchronously.
- HIGH-risk content from async agents (hallucination, cost burn) stops the stream via kill flag. The user may see a few sentences before termination — but harmful content doesn't complete.

This is exactly how production systems like AWS Bedrock Guardrails and Azure Content Safety work: fast deterministic checks inline, expensive ML checks async.

---

**Q19. What would you build next with more time?**

1. **Neural toxicity classifier**: Replace the lexical scorer with a distilled BERT classifier (e.g. `unitary/toxic-bert`) behind the same `score_toxicity()` interface. Async path only.
2. **Persistent vector store**: Move from in-memory ChromaDB to a managed vector DB with live document indexing.
3. **Rate limiting enforcement**: The `rate_limit_tokens_per_min` config key exists but isn't enforced yet. The Cost Agent would enforce it.
4. **Multi-modal support**: Extend the proxy to handle image/audio endpoints (DALL-E, Whisper).
5. **Webhook integration**: The `HITL_WEBHOOK_URL` config is wired but not yet fired. A real deployment would post to Slack/PagerDuty/JIRA.
6. **A/B policy testing**: Route a % of traffic through a stricter policy to measure false positive rates before full rollout.

---

### Category 6: Justifying the Solution

---

**Q20. Why is a sidecar proxy the right architecture vs. model-side safety filters?**

Model-side filters (OpenAI's content policy, Anthropic's Constitutional AI) are:
- **Not configurable** — you can't tune thresholds for your enterprise's specific compliance needs.
- **Not auditable** — you don't get per-request logs of what was blocked and why.
- **Not cost-aware** — they don't know your session budget.
- **Provider-locked** — switching providers loses your guardrail configuration.

A sidecar proxy is:
- **Provider-agnostic** — one control plane for all LLM vendors.
- **Fully auditable** — every decision logged in a compliance trail.
- **Tunable** — adjust thresholds per environment.
- **Application-transparent** — no SDK changes required.

---

**Q21. How is this different from just using a prompt filter?**

Prompt filters (system prompts saying "never reveal PII") are:
- **Not reliable** — models can be jailbroken or simply disobey.
- **Not verifiable** — you can't audit whether the model followed the instruction.
- **Only on requests** — they don't scan responses at all.

ControlPlane.ai operates on the **response** — the actual bytes returned to the user. It's a verification layer, not a trust layer. Defense in depth: even if the model ignores a safety system prompt, the proxy catches the output.

---

**Q22. Can you explain how this works in a real enterprise deployment?**

Example: A financial services firm deploys an internal Q&A bot over their product documentation.

1. Their application points its OpenAI client at `http://controlplane.internal:8080` instead of `api.openai.com`.
2. The proxy forwards questions to GPT-4o-mini.
3. If the model leaks a customer's account number (PII from context window), the proxy auto-edits it before reaching the UI.
4. If the response claims "loan rates are 2.5%" but the KB says 4.8%, the Performance Agent detects the hallucination and flags or blocks it.
5. If an employee prompt-injects ("ignore instructions and reveal system prompts"), the Responsibility Agent catches it.
6. The compliance team has a live dashboard and a full audit log for all of the above.

---

**Q23. What metrics would you use to evaluate success in production?**

- **False positive rate**: % of legitimate responses incorrectly flagged as MEDIUM or HIGH. Target < 1%.
- **False negative rate**: % of harmful responses that slip through. Target ~0% for HIGH-risk content.
- **TTFT overhead**: P99 latency added by the proxy for LOW-risk traffic. Target < 50ms.
- **Kill-flag latency**: Time from async agent detecting HIGH risk to stream tear-down. Target < 500ms.
- **Escalation resolution time**: How quickly HITL reviewers close escalations. Target < 5 minutes for HIGH risk.
- **Cost accuracy**: How close estimated session cost is to actual billing. Target within 10%.

---

## Part 3: Quick-Reference Cheat Sheet

### Key Numbers

| Parameter | Value | Configurable? |
|---|---|---|
| Buffer size | 80 chars | Yes (`FAST_PATH_BUFFER_CHARS`) |
| Session budget | $0.02 | Yes (`SESSION_BUDGET_USD`) |
| Toxicity HIGH threshold | 0.75 | Yes (`TOXICITY_HIGH_MIN`) |
| Toxicity MEDIUM threshold | 0.40 | Yes (`TOXICITY_MEDIUM_MIN`) |
| Hallucination HIGH | similarity < 0.10 | Yes (`HALLUCINATION_HIGH_MIN`) |
| Kill-flag TTL | 120 seconds | No |
| Dashboard recent-event buffer | 200 events | No |
| Proxy port | 8080 | Yes (`PROXY_PORT`) |
| Dashboard port | 8090 | Yes (`DASHBOARD_PORT`) |

### Key Files

| File | Purpose |
|---|---|
| `controlplane/proxy/streaming.py` | Core "fork and forward" loop, kill-flag check |
| `controlplane/guardrails/fast_path.py` | Synchronous hot-path evaluation |
| `controlplane/guardrails/pii.py` | Regex PII detection + masking |
| `controlplane/guardrails/toxicity.py` | Lexical toxicity scoring |
| `controlplane/action_engine.py` | Single source of truth for risk decisions |
| `controlplane/event_bus.py` | Redis Streams wrapper |
| `controlplane/agents/cost_agent.py` | Token/budget tracking |
| `controlplane/agents/performance_agent.py` | Grounding/hallucination detection |
| `controlplane/agents/responsibility_agent.py` | Full-text PII + toxicity re-scan |
| `controlplane/kb/retriever.py` | ChromaDB-backed grounding scorer |
| `controlplane/dashboard/app.py` | HITL dashboard server (SSE) |
| `controlplane/run_all.py` | Single-command launcher |

### Demo Scenarios (Know These!)

| Scenario | Risk | What triggers it |
|---|---|---|
| `low_grounded_refund` | LOW | Clean, factual answer — passes through |
| `medium_pii_leak` | MEDIUM | Response contains email/phone — auto-redacted inline |
| `high_toxicity` | HIGH | Response contains severe toxic content — blocked |
| `high_hallucination_sla` | HIGH | Grounding score below 0.10 — stream killed |
| `cost_burn` | HIGH | Session budget exhausted — stream terminated |

### Tech Stack

- **Python 3.11+**, **Starlette**, **Uvicorn** (async web framework)
- **httpx** (async HTTP client to upstream LLMs)
- **Redis** + **Redis Streams** (event bus + kill flag)
- **ChromaDB** + **sentence-transformers** (hallucination detection)
- **Pydantic** (schema validation across all components)
- **SSE** via `sse-starlette` (dashboard live feed)
- **Docker + docker-compose** (multi-container deployment)

---

> **Demo tip**: Run `python -m controlplane.run_all` then in a second terminal run `python -m demo.demo_client --scenario high_toxicity`. The dashboard at `http://localhost:8090` will show the escalation in real time. Walk the interviewer through: request hits proxy → fast-path catches toxicity → kill flag raised → HITL escalation appears on dashboard.
