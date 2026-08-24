"""
CLI demo client: drives one of the canned mock scenarios through the
sidecar proxy and prints exactly what the end user would have received —
including watching a MEDIUM auto-edit happen inline, or a HIGH block cut
the stream short. Run the dashboard in a browser tab alongside this to
watch the corresponding action/escalation appear live.

Usage:
    python -m demo.demo_client --list
    python -m demo.demo_client --scenario high_toxicity
    python -m demo.demo_client --scenario high_pii_multi --provider anthropic
    python -m demo.demo_client --all              # run every scenario back to back
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx

from .scenarios import SCENARIOS, get

DEFAULT_URL = "http://127.0.0.1:8080"


def _openai_body(model: str, prompt: str) -> dict:
    return {"model": model, "stream": True, "messages": [{"role": "user", "content": prompt}]}


def _anthropic_body(model: str, prompt: str) -> dict:
    return {"model": model, "stream": True, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}


async def run_scenario(base_url: str, scenario_id: str, provider: str) -> None:
    scenario = get(scenario_id)
    model = f"mock:{scenario_id}"
    path = "/v1/chat/completions" if provider == "openai" else "/v1/messages"
    body = _openai_body(model, scenario["user_prompt"]) if provider == "openai" else _anthropic_body(model, scenario["user_prompt"])

    print("=" * 78)
    print(f"SCENARIO   : {scenario_id}  (expected tier: {scenario['expected_tier']})")
    print(f"PROVIDER   : {provider}   PATH: {path}")
    print(f"USER PROMPT: {scenario['user_prompt']}")
    print(f"WATCH FOR  : {scenario['what_to_look_for']}")
    print("-" * 78)
    print("MODEL OUTPUT (as the end user would see it):\n")

    start = time.time()
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", base_url + path, json=body) as resp:
            trace_id = resp.headers.get("x-controlplane-trace-id", "?")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                obj = json.loads(data)
                text = None
                if provider == "openai":
                    text = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                else:
                    delta = obj.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text")
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
    elapsed = time.time() - start
    print(f"\n\n[trace_id={trace_id}  elapsed={elapsed:.2f}s]")
    print("=" * 78 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="ControlPlane.ai demo client")
    parser.add_argument("--url", default=DEFAULT_URL, help="Sidecar proxy base URL")
    parser.add_argument("--scenario", help="Scenario id to run (see --list)")
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--list", action="store_true", help="List available scenarios and exit")
    parser.add_argument("--all", action="store_true", help="Run every scenario back to back")
    args = parser.parse_args()

    if args.list or not (args.scenario or args.all):
        print("Available scenarios:\n")
        for s in SCENARIOS:
            print(f"  {s['id']:26s} [{s['expected_tier']}]")
        print("\nRun with --scenario <id>, or --all to run every one back to back.")
        return

    async def go():
        if args.all:
            for s in SCENARIOS:
                await run_scenario(args.url, s["id"], args.provider)
        else:
            await run_scenario(args.url, args.scenario, args.provider)

    asyncio.run(go())


if __name__ == "__main__":
    main()
