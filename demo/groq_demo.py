import asyncio
import httpx
import sys
import json
import time

PROXY_URL = "http://127.0.0.1:8080/v1/chat/completions"

async def main():
    print("=" * 78)
    print("SCENARIO   : Real Groq API traffic (COST BURN test)")
    print("PROVIDER   : Groq (via OpenAI-compatible proxy endpoint)")
    print("MODEL      : qwen/qwen3.6-27b")
    print("USER PROMPT: Write a very detailed, 50-paragraph essay on the history of the universe.")
    print("=" * 78)
    print("Sending request to proxy... (Make sure your server is running!)")
    print("-" * 78)
    
    body = {
        "model": "qwen/qwen3.6-27b",
        "stream": True,
        "messages": [
            {
                "role": "user",
                "content": "Write a very detailed, 50-paragraph essay on the history of the universe."
            }
        ]
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", PROXY_URL, json=body) as resp:
                trace_id = resp.headers.get("x-controlplane-trace-id", "?")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    obj = json.loads(data)
                    text = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                    if text:
                        sys.stdout.write(text)
                        sys.stdout.flush()
    except httpx.ConnectError:
        print("\n\n[!] Error: Could not connect to the proxy at 127.0.0.1:8080.")
        print("    Please ensure you have started the server with `python -m controlplane.run_all`")
        return

    elapsed = time.time() - start
    print(f"\n\n[trace_id={trace_id}  elapsed={elapsed:.2f}s]")
    print("=" * 78 + "\n")
    print("TIP: Check your dashboard at http://localhost:8090 to see the exact token usage and session cost recorded by the Cost Agent using tiktoken!")

if __name__ == "__main__":
    asyncio.run(main())
