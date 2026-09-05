# ControlPlane.ai

A non-blocking sidecar proxy that evaluates outbound LLM traffic in real time, blocks unsafe output when needed, and exposes the findings through a live dashboard.

## 🎬 Demo Video

Watch the project demonstration video: [**`23je0841_ControlPlane.ai_R2.mp4`**](<Video : PPT submissions/23je0841_ControlPlane.ai_R2.mp4>)

---

## 1. Implementation approach

The solution sits between the application and the upstream model provider as a sidecar proxy. It does not treat the model as trusted by default; instead, it intercepts requests and responses, evaluates them across multiple guardrail dimensions, and enforces a clear risk policy:

- Low risk: allow the request through normally.
- Medium risk: edit or redact the content inline where appropriate.
- High risk: block the response and escalate it for human review.

This is implemented in three layers:

1. Fast-path guardrails in the request/response flow
   - Runs synchronously on streamed chunks.
   - Catches obvious toxicity and high-confidence PII quickly.
   - Keeps latency low and avoids blocking the happy path.

2. Asynchronous micro-agents
   - Consume events from Redis Streams.
   - Evaluate cost, hallucination/grounding, and responsibility signals.
   - Trigger escalation or kill flags when a more expensive, deeper analysis identifies a risk.

3. Action engine and human escalation
   - Maps findings to LOW / MEDIUM / HIGH risk.
   - Converts the risk tier to PASS / EDIT / BLOCK_AND_ESCALATE.
   - Surfaces escalations in the dashboard for manual review.

## 2. Solution architecture

```text
Application
   │
   ▼
Sidecar Proxy (ControlPlane.ai)
   ├─ forwards traffic to upstream model
   ├─ inspects streamed chunks in real time
   ├─ masks or blocks unsafe outputs when needed
   └─ publishes events to Redis Streams
             │
             ▼
      ┌───────────────┐
      │ Cost Agent    │
      ├───────────────┤
      │ Performance   │
      │ Agent         │
      ├───────────────┤
      │ Responsibility│
      │ Agent         │
      └──────┬────────┘
             │
             ▼
      Dynamic Action Engine
             │
             ├─ PASS
             ├─ EDIT
             └─ BLOCK_AND_ESCALATE
                     │
                     ▼
               HITL Dashboard
```

The proxy is built to preserve user experience while still enforcing a strict safety policy. Cheap checks run in the hot path, while deeper reasoning executes asynchronously and can still stop an in-flight stream when necessary.

## 3. Core components

### Proxy layer
- `controlplane/proxy/app.py`: HTTP routes and proxy entrypoints.
- `controlplane/proxy/streaming.py`: request interception, chunk buffering, masking logic, and kill-flag enforcement.

### Risk evaluation and actioning
- `controlplane/action_engine.py`: central decision logic for LOW / MEDIUM / HIGH risk.
- `controlplane/guardrails/fast_path.py`: synchronous guardrail checks.
- `controlplane/guardrails/pii.py`: PII and secret detection.
- `controlplane/guardrails/toxicity.py`: toxicity scoring heuristics.

### Agents
- `controlplane/agents/cost_agent.py`: measures token usage and budget burn.
- `controlplane/agents/performance_agent.py`: checks grounding and hallucination risk.
- `controlplane/agents/responsibility_agent.py`: inspects policy violations, PII leakage, and toxic content.

### Event bus and telemetry
- `controlplane/event_bus.py`: Redis Streams event bus and consumer group logic.
- `controlplane/dashboard/app.py`: dashboard server and streaming endpoints.
- `controlplane/dashboard/static/index.html`: live dashboard UI.
- `controlplane/dashboard/static/simulator.html`: interactive simulator page.

## 4. Dependencies

The project uses the following main dependencies:

- Python 3.11+
- Starlette
- Uvicorn
- sse-starlette
- httpx
- pydantic
- python-dotenv
- Redis
- scikit-learn
- numpy
- chromadb
- tiktoken

All required libraries are listed in [requirements.txt](requirements.txt).

## 5. Execution instructions

### Local development

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start Redis locally:

```bash
redis-server --daemonize yes
```

4. Run the full stack:

```bash
python3 -m controlplane.run_all
```

This starts:
- sidecar proxy on `http://localhost:8080`
- dashboard on `http://localhost:8090`
- evaluation workers and event bus consumers

### Docker deployment

```bash
docker-compose up --build
```

This is the quickest way to run the app in a multi-service topology similar to a real deployment.

## 6. Demo usage

The project includes deterministic mock scenarios for quick testing without external API keys.

List demo scenarios:

```bash
python3 -m demo.demo_client --list
```

Run a specific scenario:

```bash
python3 -m demo.demo_client --scenario high_toxicity
```

Run the full set:

```bash
python3 -m demo.demo_client --all
```

The simulator page is also available at:

- `http://localhost:8090/simulator`

## 7. Testing

Run the unit tests with:

```bash
pytest -q
```

If the environment is not set up the same as the repo defaults, also ensure the project root is on the Python path:

```bash
export PYTHONPATH="."
pytest -q
```

## 8. Project structure

```
ControlPlane.ai/
├── controlplane/
│   ├── agents/
│   ├── dashboard/
│   ├── guardrails/
│   ├── kb/
│   ├── providers/
│   ├── proxy/
│   ├── __init__.py
│   ├── action_engine.py
│   ├── config.py
│   ├── event_bus.py
│   ├── run_agent.py
│   ├── run_all.py
│   ├── schemas.py
│   └── util.py
├── demo/
├── resources/
├── submissions/
├── tests/
├── Dockerfile
├── README.md
├── PROJECT_OVERVIEW.md
├── PROJECT_OVERVIEW_TEMPLATE.md
├── docker-compose.yml
├── requirements.txt
└── .env
```

## 9. Notes

- The project is designed to be explainable and auditable, not just reactive.
- The proxy supports both OpenAI-style and Anthropic-style upstream request formats.
- The mock provider is useful for demos and local testing when no API key is available.

For a fuller historical and architectural write-up, see [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).
