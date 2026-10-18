"""Structured events and in-process metrics.

Every event is one JSON line on stderr (never stdout, which the MCP server uses
for protocol messages) and is counted in memory. Fields carry ids, statuses,
and reason codes only. No credential values exist in this project, and none
are logged.

Metrics are per process and reset when the process restarts. They are for the
demo and for spotting trouble, not a durable record. The durable record is the
Plan, Decision, Execution, and Observation nodes in FalkorDB.
"""

import json
import logging
import sys
import threading
from collections import Counter, deque
from datetime import datetime, timezone

_LOGGER = logging.getLogger("actiongraph.events")
if not _LOGGER.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(_handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False

_LOCK = threading.Lock()
_COUNTERS = Counter()
_BLOCK_REASONS = Counter()
_LATENCIES = {"preflight_ms": deque(maxlen=1000)}
_RECENT = deque(maxlen=50)

STATUS_EVENTS = {
    "VERIFIED": "plan.verified",
    "PARTIAL_FAILURE": "execution.partial_failure",
    "UNKNOWN": "execution.unknown",
    "FAILED": "execution.failed",
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def emit(event, **fields):
    record = {"ts": _now(), "event": event}
    record.update({key: value for key, value in fields.items() if value is not None})
    with _LOCK:
        _COUNTERS[event] += 1
        if event == "invariant.blocked" and fields.get("reason_code"):
            _BLOCK_REASONS[fields["reason_code"]] += 1
        _RECENT.append(record)
    _LOGGER.info(json.dumps(record, default=str, sort_keys=True))
    return record


def observe(metric, value_ms):
    with _LOCK:
        _LATENCIES[metric].append(float(value_ms))


def _summary(values):
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)

    def pick(fraction):
        return round(ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))], 2)

    return {"count": len(ordered), "p50": pick(0.5), "p95": pick(0.95), "max": round(ordered[-1], 2)}


def snapshot():
    with _LOCK:
        counters = dict(_COUNTERS)
        block_reasons = dict(_BLOCK_REASONS)
        latencies = {name: _summary(list(values)) for name, values in _LATENCIES.items()}
        recent = list(_RECENT)
    finished = sum(counters.get(name, 0) for name in STATUS_EVENTS.values())
    preflights = counters.get("plan.preflighted", 0)
    return {
        "scope": "this process since it started",
        "counters": counters,
        "preflights": preflights,
        "blocked": counters.get("invariant.blocked", 0),
        "block_reasons": block_reasons,
        "needs_review": counters.get("plan.needs_review", 0),
        "executions_finished": finished,
        "verified": counters.get("plan.verified", 0),
        "verification_rate": round(counters.get("plan.verified", 0) / finished, 3) if finished else None,
        "unknown_outcomes": counters.get("execution.unknown", 0),
        "retries": counters.get("action.retried", 0),
        "duplicates_prevented": counters.get("execution.duplicate_prevented", 0),
        "graph_unavailable": counters.get("graph.unavailable", 0),
        "latency_ms": latencies,
        "recent_events": recent,
    }


def reset_metrics():
    with _LOCK:
        _COUNTERS.clear()
        _BLOCK_REASONS.clear()
        for values in _LATENCIES.values():
            values.clear()
        _RECENT.clear()
