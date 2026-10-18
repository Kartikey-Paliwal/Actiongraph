import os

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


def pytest_sessionfinish(session, exitstatus):
    """With REQUIRE_GRAPH=1 (set in CI), a skipped graph test fails the run.

    Locally a missing FalkorDB skips the graph tests. In CI that would look
    green while testing almost nothing, so it counts as a failure there.
    """
    if os.environ.get("REQUIRE_GRAPH") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = len(reporter.stats.get("skipped", [])) if reporter else 0
    if skipped:
        if reporter:
            reporter.write_line("REQUIRE_GRAPH=1: %s test(s) skipped, failing the run" % skipped, red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
