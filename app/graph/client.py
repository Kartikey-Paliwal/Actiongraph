"""FalkorDB connection helpers."""

from falkordb import FalkorDB

from app.config import settings


def connect():
    cfg = settings()
    return FalkorDB(
        host=cfg["host"],
        port=cfg["port"],
        socket_connect_timeout=2,
        socket_timeout=5,
    )


def graph(db=None):
    client = db if db is not None else connect()
    return client.select_graph(settings()["graph"])


def ping(db=None):
    """Raise if FalkorDB does not answer. Impact checks must fail closed."""
    client = db if db is not None else connect()
    connection = getattr(client, "connection", client)
    if not connection.ping():
        raise RuntimeError("FalkorDB did not respond to PING")
    return client
