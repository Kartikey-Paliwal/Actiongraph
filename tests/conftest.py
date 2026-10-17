import pytest

from app.graph.client import connect, ping
from app.graph.seed import reset_scenario


@pytest.fixture
def fresh_graph():
    try:
        client = ping(connect())
    except Exception as exc:
        pytest.skip("FalkorDB is not reachable: %s" % exc)
    reset_scenario(client)
    return client
