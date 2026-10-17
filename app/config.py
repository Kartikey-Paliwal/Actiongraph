"""Runtime settings. Values are read on each call so tests can override them."""

import os


def _int_env(name, default):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("%s must be an integer" % name) from exc
    if value <= 0:
        raise RuntimeError("%s must be positive" % name)
    return value


def settings():
    return {
        "host": os.environ.get("FALKORDB_HOST", "127.0.0.1"),
        "port": _int_env("FALKORDB_PORT", 6379),
        "graph": os.environ.get("FALKORDB_GRAPH", "actiongraph"),
        "query_timeout_ms": _int_env("QUERY_TIMEOUT_MS", 2000),
        "result_limit": _int_env("RESULT_LIMIT", 100),
    }
