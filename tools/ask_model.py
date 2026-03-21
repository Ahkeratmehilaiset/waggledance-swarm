"""Send a single chat query to WaggleDance and print the response."""
import asyncio
import sys
import os

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, ".")

from hivemind import HiveMind


async def main():
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Mika paiva tanaan on?"
    print(f"Q: {question}", flush=True)
    print("-" * 60, flush=True)

    hive = HiveMind("configs/settings.yaml")
    await hive.start()

    print("[INFO] HiveMind started, sending chat...", flush=True)

    try:
        response = await asyncio.wait_for(
            hive.chat(question, language="fi"),
            timeout=120.0,
        )
        print(f"A: {response}", flush=True)
    except asyncio.TimeoutError:
        print("[TIMEOUT] chat() did not respond within 120s", flush=True)
        # Try direct _do_chat bypassing priority lock
        print("[RETRY] Bypassing priority lock...", flush=True)
        try:
            response = await asyncio.wait_for(
                hive._do_chat(question, language="fi"),
                timeout=60.0,
            )
            print(f"A: {response}", flush=True)
        except asyncio.TimeoutError:
            print("[TIMEOUT] _do_chat also timed out after 60s", flush=True)
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)

    print("-" * 60, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
