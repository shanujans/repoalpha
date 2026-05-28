"""
utils/logger.py — Enterprise Structured Logging
RepoAlpha

Uses loguru for human-readable logs locally and JSON-structured logs
in CI/production. Also writes a pipeline_runs audit trail to Supabase
so you can inspect every agent run from the dashboard.

Zero-cost: loguru is pure Python, no paid logging service.
"""

import sys
import os
import json
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Generator
from loguru import logger

# ─── Configure loguru ────────────────────────────────────────────────────────

# Remove the default stderr sink
logger.remove()

# Human-readable for local dev
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
           "<cyan>{extra[phase]}</cyan> | {message}",
    level="DEBUG",
    colorize=True,
)

# JSON file sink — rotate daily, keep 7 days (zero storage cost)
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger.add(
    os.path.join(LOG_DIR, "pipeline_{time:YYYY-MM-DD}.jsonl"),
    format="{message}",                # raw JSON per line
    level="INFO",
    rotation="00:00",                  # new file at midnight
    retention="7 days",
    serialize=True,                    # loguru auto-serializes to JSON
)


def get_logger(phase: str):
    """Returns a bound logger with a phase context tag."""
    return logger.bind(phase=phase)


# ─── Supabase Audit Trail ────────────────────────────────────────────────────

def log_pipeline_run(
    supabase_client,
    phase: str,
    status: str,
    *,
    repos_processed: int = 0,
    stargazers_processed: int = 0,
    signals_detected: int = 0,
    alerts_fired: int = 0,
    error_message: str | None = None,
    duration_seconds: float | None = None,
    started_at: datetime | None = None,
) -> None:
    """
    Writes a pipeline run record to Supabase `pipeline_runs` table.
    Non-blocking: if Supabase write fails, we just log locally.
    This gives the dashboard a live activity feed.
    """
    try:
        supabase_client.table("pipeline_runs").insert({
            "phase": phase,
            "status": status,
            "repos_processed": repos_processed,
            "stargazers_processed": stargazers_processed,
            "signals_detected": signals_detected,
            "alerts_fired": alerts_fired,
            "error_message": error_message,
            "duration_seconds": duration_seconds,
            "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.bind(phase="logger").warning(f"Audit log write failed: {e}")


# ─── Context Manager for Timed Pipeline Phases ──────────────────────────────

@contextmanager
def pipeline_phase(
    supabase_client,
    phase: str,
    **kwargs,
) -> Generator[dict, None, None]:
    """
    Context manager that times a pipeline phase and writes audit log.

    Usage:
        with pipeline_phase(supabase, "harvest") as ctx:
            # ... do work ...
            ctx["repos_processed"] = 50
    """
    log = get_logger(phase)
    started = datetime.now(timezone.utc)
    ctx: dict = {
        "repos_processed": 0,
        "stargazers_processed": 0,
        "signals_detected": 0,
        "alerts_fired": 0,
    }

    log.info(f"Phase [{phase}] started")
    try:
        yield ctx
        duration = (datetime.now(timezone.utc) - started).total_seconds()
        log.success(
            f"Phase [{phase}] completed in {duration:.1f}s | "
            f"repos={ctx['repos_processed']} "
            f"signals={ctx['signals_detected']}"
        )
        log_pipeline_run(
            supabase_client,
            phase=phase,
            status="success",
            duration_seconds=duration,
            started_at=started,
            **ctx,
        )
    except Exception as exc:
        duration = (datetime.now(timezone.utc) - started).total_seconds()
        log.error(f"Phase [{phase}] FAILED after {duration:.1f}s: {exc}")
        log_pipeline_run(
            supabase_client,
            phase=phase,
            status="failed",
            error_message=str(exc)[:500],
            duration_seconds=duration,
            started_at=started,
        )
        raise
