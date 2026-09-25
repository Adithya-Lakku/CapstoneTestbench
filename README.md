# AIOps Testbench — Box 1 (Target Environment)

A small, educational Docker-based testbench that produces **real, observable failures** for a future AIOps self-healing platform.

This is **Box 1** of a larger architecture:

```
Box 1 → Box 2 → Kafka → Box 3 → Box 4 → Box 1
```

Right now we are building only Box 1.

---

## What is Box 1?

Box 1 is the **target environment** — the system that the AIOps platform will eventually monitor, diagnose, and heal. It contains:

- A real web application (FastAPI)
- A real database (PostgreSQL)
- Real metrics collection (Prometheus + cAdvisor)
- Real traffic (load generator)
- Controllable failure injection (stress endpoints)

At its core it looks like a **normal application stack**. The failure-injection endpoints turn it into a testbench.

---

## Architecture

```
                    BOX 1 — TESTBENCH

    ┌────────────────┐         ┌─────────────┐
    │ Load Generator │────────▶│  FastAPI     │
    └────────────────┘         │  (target)    │
                               │              │
                               │  /           │
                               │  /health     │
                               │  /users      │
                               │  /slow       │
                               │  /error      │
                               │  /stress/cpu │
                               │  /stress/mem │
                               │  /metrics ───┼──────┐
                               └──────┬───────┘      │
                                      │               │
                                      ▼               ▼
                               ┌─────────────┐  ┌──────────┐
                               │ PostgreSQL  │  │Prometheus│
                               └─────────────┘  └────┬─────┘
                                                      │
                                                      │ also
                                                      │ scrapes
                                                      │
                                                ┌─────┴─────┐
                                                │  cAdvisor  │
                                                │  /metrics  │
                                                └────────────┘
```

### Data flow

```
Load Generator
      │
      ▼
FastAPI ──────▶ PostgreSQL        (real DB queries)
      │
      │ exposes /metrics
      ▼
Prometheus                        (application-level metrics)
      ▲
      │ scrapes /metrics
      │
cAdvisor                          (container-level metrics)
```

---

## Services

| Service          | Image / Build           | Port (host) | Purpose                          |
|------------------|-------------------------|-------------|----------------------------------|
| `target-service` | `./target-service`      | 8000        | FastAPI application              |
| `postgres`       | `postgres:16-alpine`    | —           | Database (internal only)         |
| `prometheus`     | `prom/prometheus:v2.54` | 9090        | Metrics collection & storage     |
| `cadvisor`       | `gcr.io/cadvisor/...`   | 8080        | Docker container metrics         |
| `load-generator` | `./load-generator`      | —           | Sends HTTP traffic to FastAPI    |

---

## Quick Start

```bash
# Clone/navigate to the project directory, then:
docker compose up --build
```

After startup, open:

| URL                          | What you see                     |
|------------------------------|----------------------------------|
| http://localhost:8000        | FastAPI root response             |
| http://localhost:8000/users  | Users from PostgreSQL             |
| http://localhost:8000/metrics| Prometheus metrics (text format)  |
| http://localhost:9090        | Prometheus web UI                 |
| http://localhost:8080        | cAdvisor web UI                   |

To stop everything:

```bash
docker compose down
```

To stop and remove all data (volumes):

```bash
docker compose down -v
```

---

## Networking: `localhost` vs service names

This is one of the most important concepts to understand.

### From your host machine (browser)

Your browser runs on the **host OS**, outside Docker. You use `localhost` with port mappings:

```
Browser  →  localhost:8000  →  Docker port mapping  →  target-service container
```

### Inside Docker (container-to-container)

Containers on the same Docker network reach each other by **service name**:

```
Prometheus container  →  target-service:8000  →  FastAPI container
Prometheus container  →  cadvisor:8080        →  cAdvisor container
FastAPI container     →  postgres:5432        →  PostgreSQL container
Load generator        →  target-service:8000  →  FastAPI container
```

**Why?** Docker Compose creates a DNS entry for each service name on the shared network (`testbench-net`). `localhost` inside a container refers to *that container itself*, not to the host or to other containers.

### Visual summary

```
┌─── HOST MACHINE ────────────────────────────────┐
│                                                  │
│  Browser → localhost:8000 ──┐                    │
│  Browser → localhost:9090 ──┼── port mappings    │
│  Browser → localhost:8080 ──┘                    │
│                                                  │
│  ┌─── DOCKER NETWORK (testbench-net) ─────────┐ │
│  │                                             │ │
│  │  target-service:8000                        │ │
│  │  postgres:5432                              │ │
│  │  prometheus:9090                            │ │
│  │  cadvisor:8080                              │ │
│  │  load-generator (no exposed port)           │ │
│  │                                             │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

---

## Endpoints

### Normal endpoints

| Endpoint    | Method | Description                           |
|-------------|--------|---------------------------------------|
| `/`         | GET    | Service info — confirms app is alive  |
| `/health`   | GET    | Health check — pings the database     |
| `/users`    | GET    | Returns all users from PostgreSQL     |

### Failure-injection endpoints

| Endpoint          | Method | Description                              |
|-------------------|--------|------------------------------------------|
| `/slow?delay=3`   | GET    | Sleeps for `delay` seconds (default: 3)  |
| `/error`          | GET    | Returns HTTP 500 deliberately            |
| `/stress/cpu?duration=10` | GET | Burns CPU for `duration` seconds  |
| `/stress/memory?mb=100&duration=30` | GET | Allocates `mb` MB for `duration` seconds |

---

## Metrics

### Two sources of metrics

| Source          | Provides                     | Scraped at              |
|-----------------|------------------------------|-------------------------|
| FastAPI `/metrics` | Application-level telemetry | `target-service:8000`   |
| cAdvisor `/metrics`| Container-level telemetry   | `cadvisor:8080`         |

### Application metrics (from FastAPI)

| Metric                              | Type      | Why this type?                                    |
|-------------------------------------|-----------|---------------------------------------------------|
| `http_requests_total`               | Counter   | Total requests only go up; use `rate()` for RPS   |
| `http_request_duration_seconds`     | Histogram | Distribution of durations; enables p50/p95/p99    |
| `http_active_requests`              | Gauge     | Current in-flight requests; goes up AND down      |
| `db_queries_total`                  | Counter   | Total DB queries only go up                       |
| `db_query_duration_seconds`         | Histogram | Distribution of DB query times                    |

**Why Counter?** Counters monotonically increase. You never reset them — Prometheus uses `rate()` to compute per-second rates. Perfect for "total requests" and "total errors".

**Why Histogram?** Histograms record each value into configurable buckets. Prometheus auto-creates `_bucket`, `_count`, and `_sum` sub-metrics. You can compute percentiles (p50, p95, p99) with `histogram_quantile()`.

**Why Gauge?** Gauges go up and down. Perfect for "currently active requests" — a snapshot of the present, not a cumulative total.

### Container metrics (from cAdvisor)

cAdvisor automatically exposes metrics for every Docker container:

| Metric                                    | What it measures                    |
|-------------------------------------------|-------------------------------------|
| `container_cpu_usage_seconds_total`        | Cumulative CPU time consumed        |
| `container_memory_usage_bytes`             | Current memory usage (RSS + cache)  |
| `container_memory_working_set_bytes`       | Memory actually in use (no cache)   |
| `container_network_receive_bytes_total`    | Total bytes received                |
| `container_network_transmit_bytes_total`   | Total bytes transmitted             |
| `container_fs_usage_bytes`                 | Filesystem usage                    |

---

## PromQL Queries

Open Prometheus at http://localhost:9090 and try these queries.

### Target health

```promql
up
```
Shows whether Prometheus can reach each scrape target. `1` = up, `0` = down.

### Total requests

```promql
http_requests_total
```
The raw counter value — total requests since the service started, broken down by `method`, `endpoint`, and `status_code`.

### Request rate (RPS)

```promql
rate(http_requests_total[1m])
```
Per-second request rate averaged over the last 1 minute. `rate()` takes a counter and returns a per-second derivative.

### Error rate (5xx)

```promql
rate(http_requests_total{status_code="500"}[1m])
```
Per-second rate of HTTP 500 responses. After calling `/error` a few times, this value will be non-zero.

### Total error ratio

```promql
sum(rate(http_requests_total{status_code="500"}[5m]))
/
sum(rate(http_requests_total[5m]))
```
Fraction of all requests that resulted in a 500 error over the last 5 minutes.

### Request latency (p95)

```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```
The 95th percentile request duration over the last 5 minutes. After calling `/slow?delay=5`, this will jump.

### Average request duration

```promql
rate(http_request_duration_seconds_sum[5m])
/
rate(http_request_duration_seconds_count[5m])
```
Average request duration. `_sum` is the total accumulated duration; `_count` is the total number of observations.

### Active requests

```promql
http_active_requests
```
Number of requests currently being processed. This is a Gauge — it reflects the present moment.

### Database query rate

```promql
rate(db_queries_total[1m])
```
Database queries per second. The `operation` label tells you which endpoint triggered the query.

### Database query latency (p95)

```promql
histogram_quantile(0.95, rate(db_query_duration_seconds_bucket[5m]))
```
95th percentile DB query duration.

### Container CPU usage (target-service)

```promql
rate(container_cpu_usage_seconds_total{name="aiops-testbench-target-service-1"}[1m])
```
CPU cores consumed by the target-service container, per second. After calling `/stress/cpu`, this will spike.

> **Note:** The container name may vary. Check with `container_cpu_usage_seconds_total` (no filter) first to find the exact name.

### Container memory usage

```promql
container_memory_working_set_bytes{name="aiops-testbench-target-service-1"}
```
Current memory usage (in bytes) of the target-service container. After calling `/stress/memory`, this will increase temporarily.

---

## Failure Scenarios — How to Use Them

Each failure endpoint creates a **real** observable effect. The metrics are not faked.

### 1. Latency injection

```bash
curl "http://localhost:8000/slow?delay=5"
```

**Causal chain:**
```
/slow called → actual sleep(5) → request duration = 5 seconds
    → http_request_duration_seconds histogram records 5.0
    → Prometheus observes higher latency
```

**Verify in Prometheus:**
```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{endpoint="/slow"}[5m]))
```

### 2. Error injection

```bash
curl "http://localhost:8000/error"
```

**Causal chain:**
```
/error called → actual HTTP 500 response
    → http_requests_total{status_code="500"} counter incremented
    → Prometheus observes rising error rate
```

**Verify in Prometheus:**
```promql
rate(http_requests_total{status_code="500"}[1m])
```

### 3. CPU stress

```bash
curl "http://localhost:8000/stress/cpu?duration=30"
```

**Causal chain:**
```
/stress/cpu called → tight arithmetic loop for 30 seconds
    → actual CPU consumption in the container
    → cAdvisor reads cgroup CPU counters → exposes via /metrics
    → Prometheus scrapes cAdvisor → stores container_cpu_usage_seconds_total
```

**Verify in Prometheus:**
```promql
rate(container_cpu_usage_seconds_total{name=~".*target-service.*"}[1m])
```

### 4. Memory stress

```bash
curl "http://localhost:8000/stress/memory?mb=200&duration=60"
```

**Causal chain:**
```
/stress/memory called → allocates 200 MB of bytearrays
    → actual memory consumption in the container
    → cAdvisor reads cgroup memory counters → exposes via /metrics
    → Prometheus scrapes cAdvisor → stores container_memory_working_set_bytes
    → after 60 seconds, memory is released (garbage collected)
```

**Verify in Prometheus:**
```promql
container_memory_working_set_bytes{name=~".*target-service.*"}
```

### 5. High load

Change the load generator's RPS by modifying `docker-compose.yml`:

```yaml
load-generator:
  environment:
    RPS: "50"   # was 5
```

Then restart:
```bash
docker compose up -d --build load-generator
```

**Verify in Prometheus:**
```promql
rate(http_requests_total[1m])
```

---

## Load Generator

The load generator is a simple Python script that sends HTTP requests at a configurable rate.

### Configuration

| Environment Variable | Default                     | Description              |
|----------------------|-----------------------------|--------------------------|
| `TARGET_URL`         | `http://target-service:8000`| Target service URL       |
| `RPS`                | `5`                         | Requests per second      |

### Changing the RPS

**Option 1:** Edit `docker-compose.yml` and restart:
```yaml
RPS: "50"
```
```bash
docker compose up -d --build load-generator
```

**Option 2:** Scale down and run manually:
```bash
docker compose stop load-generator
# Then from your host:
pip install requests
RPS=20 python load-generator/load.py
```
(Adjust `TARGET_URL` to `http://localhost:8000` if running from the host.)

---

## Project Structure

```
aiops-testbench/
│
├── docker-compose.yml          # Orchestrates all 5 services
├── prometheus.yml              # Prometheus scrape configuration
├── README.md                   # This file
│
├── target-service/             # The FastAPI application
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       └── main.py             # All application code in one file
│
└── load-generator/             # Simple HTTP traffic generator
    ├── Dockerfile
    ├── requirements.txt
    └── load.py                 # All load-gen code in one file
```

**Why so few files?** This is an educational testbench. Every file has a clear purpose. You should be able to read the entire project in 15 minutes.

---

## Verification Checklist

After running `docker compose up --build`, verify each item:

```
[ ] docker compose up --build completes without errors
[ ] http://localhost:8000        →  FastAPI root response
[ ] http://localhost:8000/health →  {"status": "healthy", "database": "up"}
[ ] http://localhost:8000/users  →  list of 3 users from PostgreSQL
[ ] http://localhost:8000/metrics →  Prometheus text format metrics
[ ] http://localhost:9090        →  Prometheus web UI loads
[ ] Prometheus → Status → Targets → target-service is UP
[ ] Prometheus → Status → Targets → cadvisor is UP
[ ] Query: up  → shows both targets with value 1
[ ] Query: http_requests_total  →  shows request counts increasing
[ ] curl localhost:8000/error     →  then query rate(http_requests_total{status_code="500"}[1m])
[ ] curl localhost:8000/slow      →  then query histogram_quantile(...)
[ ] curl localhost:8000/stress/cpu?duration=30  →  then query container CPU metric
[ ] curl localhost:8000/stress/memory?mb=100    →  then query container memory metric
[ ] Load generator logs show continuous [200] responses
```

---

## What's Next? (Future Boxes)

This testbench is Box 1 of a larger architecture:

```
Box 1  →  Box 2  →  Kafka  →  Box 3  →  Box 4  →  Box 1
target     data      event     ML         healing    self-healing
environ.   pipeline  stream    engine      agent      loop
```

**Box 1 (this project):** Produces real telemetry and controllable failures.

**Box 2 (next):** Will consume Prometheus metrics and forward them as events.

**Box 3:** Will run anomaly detection and root-cause analysis.

**Box 4:** Will execute healing actions back on Box 1.

For now, focus on understanding Box 1 thoroughly. Every metric, every endpoint, every container interaction.

---

## Usage Tutorial

This tutorial walks you through the testbench end-to-end, from startup to observing real failures in Prometheus.

### Step 1: Start the testbench

```bash
cd aiops-testbench
docker compose up --build
```

Wait for all services to start. You'll see logs from all 5 containers. Look for:
- `Database initialized successfully.` from target-service
- `Load generator started` from load-generator
- `Server is ready to receive web requests` from Prometheus

### Step 2: Explore the application

Open your browser and visit each endpoint:

```
http://localhost:8000           → {"service": "aiops-testbench", "status": "running"}
http://localhost:8000/health    → {"status": "healthy", "database": "up"}
http://localhost:8000/users     → [{"id":1,"name":"Alice",...}, ...]
http://localhost:8000/metrics   → (raw Prometheus metrics text)
```

This is your "normal application". It connects to PostgreSQL, serves API requests, and exposes metrics.

### Step 3: Open Prometheus

Go to http://localhost:9090.

1. Click **Status → Targets** to see the scrape targets.
2. You should see `target-service` and `cadvisor`, both with state **UP**.
3. Go back to the **Graph** tab.

### Step 4: Run your first PromQL query

In the Prometheus query box, type:

```promql
up
```

Click **Execute**. You should see two rows, both with value `1`:
- `{job="target-service"}` — FastAPI is reachable
- `{job="cadvisor"}` — cAdvisor is reachable

### Step 5: Watch the load generator in action

The load generator has been sending ~5 requests/second in the background. Query:

```promql
rate(http_requests_total[1m])
```

You'll see per-second rates for each endpoint. The `/users` endpoint should have the highest rate (it's weighted in the load generator).

### Step 6: Inject a latency failure

Open a new terminal and run:

```bash
curl "http://localhost:8000/slow?delay=5"
```

Now query the latency in Prometheus:

```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```

The p95 latency will have jumped. You just created a **real** latency spike that Prometheus observed through actual request timing.

### Step 7: Inject errors

Run this a few times:

```bash
curl http://localhost:8000/error
curl http://localhost:8000/error
curl http://localhost:8000/error
```

Now query the error rate:

```promql
rate(http_requests_total{status_code="500"}[1m])
```

You'll see the 500 error rate appear. These are real HTTP 500 responses — the counter was incremented because the server actually returned 500.

### Step 8: Stress the CPU

```bash
curl "http://localhost:8000/stress/cpu?duration=30"
```

This starts a CPU-intensive loop inside the target-service container. Wait 30-60 seconds (Prometheus scrapes every 15 seconds), then query:

```promql
rate(container_cpu_usage_seconds_total{name=~".*target-service.*"}[1m])
```

You should see a spike in CPU usage. This is the **real causal chain**: actual CPU burn → cAdvisor reads cgroup data → Prometheus stores it.

### Step 9: Stress memory

```bash
curl "http://localhost:8000/stress/memory?mb=200&duration=60"
```

This allocates 200 MB inside the container for 60 seconds. Query:

```promql
container_memory_working_set_bytes{name=~".*target-service.*"}
```

Watch it jump up, then come back down after 60 seconds when the memory is freed.

### Step 10: Increase load

Edit `docker-compose.yml` and change the load generator RPS:

```yaml
RPS: "50"
```

Restart just the load generator:

```bash
docker compose up -d --build load-generator
```

Query the request rate:

```promql
rate(http_requests_total[1m])
```

You'll see the overall RPS jump from ~5 to ~50. This is useful for simulating traffic spikes.

### Step 11: Combine failures

The power of this testbench is combining failures to create realistic scenarios:

```bash
# Simulate a traffic spike + slow database + errors
# Terminal 1: Increase load
docker compose up -d --build load-generator  # after setting RPS=50

# Terminal 2: Add latency
curl "http://localhost:8000/slow?delay=2"

# Terminal 3: Add CPU pressure
curl "http://localhost:8000/stress/cpu?duration=60"
```

Now open Prometheus and look at multiple metrics simultaneously. You'll see correlated signals across application metrics (latency, error rate) and infrastructure metrics (CPU usage). This is exactly what a future AIOps anomaly detector will analyze.

### Step 12: Clean up

```bash
# Stop all containers
docker compose down

# Stop and delete all data
docker compose down -v
```

---

**You now have a fully functional AIOps testbench.** Every metric is real. Every failure is observable. The next step is Box 2 — building a data pipeline that consumes these metrics.
# CapstoneTestbench
