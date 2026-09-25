"""
AIOps Testbench — Load Generator
==================================
A dead-simple HTTP load generator.

It continuously sends requests to the target FastAPI service at a
configurable rate (RPS). It cycles through the normal endpoints so that
the target service produces realistic, varied telemetry.

Configuration (via environment variables):
    TARGET_URL   — base URL of the target service (default: http://target-service:8000)
    RPS          — requests per second (default: 5)

You can change RPS at any time by restarting the container with a new
environment variable, or by editing the docker-compose.yml.

This is intentionally NOT Locust or k6 — just plain Python so you can
read every line.
"""

import os
import time
import random
import requests as http

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_URL = os.getenv("TARGET_URL", "http://target-service:8000")
RPS = float(os.getenv("RPS", "5"))

# Endpoints to cycle through during normal load.
# These are the "happy path" endpoints that a real user might call.
NORMAL_ENDPOINTS = [
    "/",
    "/health",
    "/users",
    "/users",
    "/users",      # weighted: /users is the most common call
]

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    interval = 1.0 / RPS  # seconds between requests
    print(f"Load generator started — target={TARGET_URL}, rps={RPS}")
    print(f"Interval between requests: {interval:.3f}s")

    while True:
        endpoint = random.choice(NORMAL_ENDPOINTS)
        url = f"{TARGET_URL}{endpoint}"

        try:
            start = time.time()
            resp = http.get(url, timeout=10)
            elapsed = time.time() - start
            print(f"[{resp.status_code}] {endpoint}  ({elapsed:.3f}s)")
        except Exception as e:
            print(f"[ERR] {endpoint}  ({e})")

        time.sleep(interval)


if __name__ == "__main__":
    main()
