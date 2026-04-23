"""
core/logger.py — Structured JSON logging for NovaMind.
Replaces all print() calls with consistent, queryable log entries.
"""
import json
import time
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

# ─── Log Configuration ────────────────────────────────────────────────────────
LOG_LEVEL = logging.DEBUG

class StructuredFormatter(logging.Formatter):
    """Outputs each log line as a single JSON object for easy parsing."""

    LEVEL_MAP = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO",
        logging.WARNING:  "WARNING",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "level":      self.LEVEL_MAP.get(record.levelno, "INFO"),
            "agent_id":   getattr(record, "agent_id", "system"),
            "task_id":    getattr(record, "task_id", None),
            "message":    record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    logger.setLevel(LOG_LEVEL)
    logger.propagate = False
    return logger


# ─── Public Interface ─────────────────────────────────────────────────────────

class AgentLogger:
    """
    Drop-in replacement for print() inside agent code.

    Usage:
        from core.logger import AgentLogger
        log = AgentLogger("coo_agent")
        log.info("Health check complete", task_id="abc-123")
        log.error("Supabase failed", exc_info=True)
    """

    def __init__(self, agent_id: str):
        self._logger = _build_logger(f"novamind.{agent_id}")
        self._agent_id = agent_id
        self._start_times: dict[str, float] = {}

    def _extra(self, task_id: Optional[str] = None) -> dict:
        return {"agent_id": self._agent_id, "task_id": task_id}

    def debug(self, msg: str, task_id: Optional[str] = None):
        self._logger.debug(msg, extra=self._extra(task_id))

    def info(self, msg: str, task_id: Optional[str] = None):
        self._logger.info(msg, extra=self._extra(task_id))

    def warning(self, msg: str, task_id: Optional[str] = None):
        self._logger.warning(msg, extra=self._extra(task_id))

    def error(self, msg: str, task_id: Optional[str] = None, exc_info: bool = False):
        self._logger.error(msg, extra=self._extra(task_id), exc_info=exc_info)

    def start_timer(self, label: str):
        """Call at the start of an operation to record its duration."""
        self._start_times[label] = time.monotonic()

    def end_timer(self, label: str, task_id: Optional[str] = None) -> float:
        """Call at the end to log elapsed ms and return it."""
        elapsed_ms = round((time.monotonic() - self._start_times.pop(label, time.monotonic())) * 1000, 2)
        self._logger.info(
            json.dumps({"operation": label, "duration_ms": elapsed_ms}),
            extra=self._extra(task_id),
        )
        return elapsed_ms
