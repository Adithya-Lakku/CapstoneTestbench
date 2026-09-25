"""
AIOps Testbench — Target Service
=================================
A simple FastAPI application that serves as the "target environment" (Box 1)
for an AIOps self-healing platform.

This application:
  1. Serves normal API endpoints (/, /health, /users)
  2. Connects to PostgreSQL via SQLAlchemy
  3. Exposes Prometheus metrics at /metrics
  4. Provides failure-injection endpoints (/slow, /error, /stress/cpu, /stress/memory)

The failure endpoints create REAL observable effects — they don't fake metric values.
"""

import os
import time
import threading

from fastapi import FastAPI, Response, Query
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, text
from sqlalchemy.orm import declarative_base, sessionmaker

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://testbench:testbench@postgres:5432/testbench",
)

# ---------------------------------------------------------------------------
# Database setup (SQLAlchemy)
# ---------------------------------------------------------------------------
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    """A deliberately simple table — one row per user."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), nullable=False)


def init_db():
    """Create the users table and seed it with sample data."""
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        # Only seed if the table is empty
        if session.query(User).count() == 0:
            seed_users = [
                User(name="Alice", email="alice@example.com"),
                User(name="Bob", email="bob@example.com"),
                User(name="Charlie", email="charlie@example.com"),
            ]
            session.add_all(seed_users)
            session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
# Counter — monotonically increasing; good for "total requests", "total errors".
# Once incremented it never decreases; you use rate() in PromQL to get RPS.
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"],
)

# Histogram — records a distribution of values (durations).
# Prometheus auto-creates _bucket, _count, _sum sub-metrics.
# You can compute p50/p95/p99 with histogram_quantile() in PromQL.
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Gauge — can go up and down; perfect for "currently active requests".
ACTIVE_REQUESTS = Gauge(
    "http_active_requests",
    "Number of HTTP requests currently being processed",
)

# Database metrics
DB_QUERY_COUNT = Counter(
    "db_queries_total",
    "Total database queries executed",
    ["operation"],
)

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="AIOps Testbench — Target Service")


@app.on_event("startup")
def on_startup():
    """Initialize the database when the application starts."""
    # Retry a few times because postgres may still be starting
    for attempt in range(10):
        try:
            init_db()
            print("Database initialized successfully.")
            return
        except Exception as e:
            print(f"DB init attempt {attempt + 1}/10 failed: {e}")
            time.sleep(2)
    print("WARNING: Could not initialize database after 10 attempts.")


# ---------------------------------------------------------------------------
# Normal endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    """Simple root endpoint — confirms the service is running."""
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        response = {"service": "aiops-testbench", "status": "running"}
        status_code = 200
        return response
    finally:
        duration = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/").observe(duration)


@app.get("/health")
def health():
    """Health check — verifies the app and database are reachable."""
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        # Actually ping the database
        db_start = time.time()
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        finally:
            session.close()
        DB_QUERY_COUNT.labels(operation="health_check").inc()
        DB_QUERY_DURATION.labels(operation="health_check").observe(time.time() - db_start)

        status_code = 200 if db_ok else 503
        response = {"status": "healthy" if db_ok else "unhealthy", "database": "up" if db_ok else "down"}
        return JSONResponse(content=response, status_code=status_code)
    finally:
        duration = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/health", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/health").observe(duration)


@app.get("/users")
def get_users():
    """Return all users from PostgreSQL — a real database query."""
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        db_start = time.time()
        session = SessionLocal()
        try:
            users = session.query(User).all()
            result = [{"id": u.id, "name": u.name, "email": u.email} for u in users]
        finally:
            session.close()
        DB_QUERY_COUNT.labels(operation="select_users").inc()
        DB_QUERY_DURATION.labels(operation="select_users").observe(time.time() - db_start)

        status_code = 200
        return result
    finally:
        duration = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/users", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/users").observe(duration)


# ---------------------------------------------------------------------------
# Failure-injection endpoints
# ---------------------------------------------------------------------------

@app.get("/slow")
def slow(delay: float = Query(default=3.0, description="Seconds to sleep")):
    """
    Latency injection — actually sleeps for `delay` seconds.

    The causal chain:
        /slow called  →  real sleep  →  request duration increases
        →  Prometheus observes higher latency in the histogram
    """
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        time.sleep(delay)
        status_code = 200
        return {"message": f"Slept for {delay} seconds", "delay": delay}
    finally:
        duration = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/slow", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/slow").observe(duration)


@app.get("/error")
def error():
    """
    Error injection — returns a real HTTP 500.

    The causal chain:
        /error called  →  actual 500 response  →  error counter increases
        →  Prometheus observes rising 5xx rate
    """
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        status_code = 500
        return JSONResponse(
            content={"error": "Deliberately triggered internal server error"},
            status_code=500,
        )
    finally:
        duration = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/error", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/error").observe(duration)


@app.get("/stress/cpu")
def stress_cpu(duration: int = Query(default=10, description="Seconds of CPU burn")):
    """
    CPU stress — burns CPU in a tight loop for `duration` seconds.

    The causal chain:
        /stress/cpu called  →  actual CPU consumption  →  cAdvisor observes
        increased container CPU  →  Prometheus stores the metric
    """
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        # Cap duration at 120 seconds to avoid runaway stress
        duration = min(duration, 120)

        def cpu_burn(seconds):
            """Burn CPU in a background thread so the endpoint can return."""
            end_time = time.time() + seconds
            while time.time() < end_time:
                # Tight arithmetic loop — real CPU work
                _ = sum(i * i for i in range(10_000))

        # Start the CPU burn in a background thread
        thread = threading.Thread(target=cpu_burn, args=(duration,))
        thread.start()

        status_code = 200
        return {
            "message": f"CPU stress started for {duration} seconds",
            "duration": duration,
        }
    finally:
        elapsed = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/stress/cpu", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/stress/cpu").observe(elapsed)


@app.get("/stress/db")
def stress_db(duration: int = Query(default=10, description="Seconds of DB CPU burn")):
    """
    Database stress — executes expensive queries for `duration` seconds.

    The causal chain:
        /stress/db called  →  heavy SQL queries sent  →  cAdvisor observes
        increased DB container CPU  →  Prometheus stores the metric
    """
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        # Cap duration at 120 seconds to avoid runaway stress
        duration = min(duration, 120)

        def db_burn(seconds):
            """Execute heavy queries in a background thread."""
            end_time = time.time() + seconds
            session = SessionLocal()
            try:
                while time.time() < end_time:
                    query_start = time.time()
                    # An expensive Postgres query that burns CPU (MD5 hashing a million random strings)
                    # We use 100,000 iterations per query to keep the loop responsive but heavy
                    session.execute(text("SELECT count(md5(random()::text)) FROM generate_series(1, 100000);"))
                    DB_QUERY_COUNT.labels(operation="stress_db").inc()
                    DB_QUERY_DURATION.labels(operation="stress_db").observe(time.time() - query_start)
            except Exception as e:
                print(f"DB stress error: {e}")
            finally:
                session.close()

        # Start the DB burn in a background thread
        thread = threading.Thread(target=db_burn, args=(duration,))
        thread.start()

        status_code = 200
        return {
            "message": f"Database stress started for {duration} seconds",
            "duration": duration,
        }
    finally:
        elapsed = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/stress/db", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/stress/db").observe(elapsed)


@app.get("/stress/memory")
def stress_memory(
    mb: int = Query(default=100, description="Megabytes to allocate"),
    duration: int = Query(default=30, description="Seconds to hold the allocation"),
):
    """
    Memory stress — allocates `mb` megabytes and holds it for `duration` seconds.

    The causal chain:
        /stress/memory called  →  actual memory allocation  →  cAdvisor observes
        increased container memory  →  Prometheus stores the metric

    The memory is released after `duration` seconds.
    """
    start = time.time()
    ACTIVE_REQUESTS.inc()
    try:
        # Safety caps
        mb = min(mb, 512)
        duration = min(duration, 120)

        def allocate_memory(megabytes, seconds):
            """Allocate memory in a background thread and hold it."""
            # Each element in this list is ~1 MB (bytearray)
            blocks = [bytearray(1024 * 1024) for _ in range(megabytes)]
            time.sleep(seconds)
            # Python garbage-collects `blocks` when this function returns
            del blocks

        thread = threading.Thread(target=allocate_memory, args=(mb, duration))
        thread.start()

        status_code = 200
        return {
            "message": f"Allocated {mb} MB for {duration} seconds",
            "mb": mb,
            "duration": duration,
        }
    finally:
        elapsed = time.time() - start
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method="GET", endpoint="/stress/memory", status_code=status_code).inc()
        REQUEST_DURATION.labels(method="GET", endpoint="/stress/memory").observe(elapsed)


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint
# ---------------------------------------------------------------------------

@app.get("/metrics")
def metrics():
    """
    Expose all Prometheus metrics in the text exposition format.

    Prometheus scrapes this endpoint every `scrape_interval`.
    """
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
