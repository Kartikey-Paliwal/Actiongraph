# ActionGraph — status

Status date: 17 October 2026, Pacific time (18 October, India time).

Hackathon: [Graph Hacks: Context for AI Agents](https://www.wemakedevs.org/hackathons/falkordb), Track 01, Agents That Act on Connected Data. Submissions close 18 October 2026, 11:59 PM IST.

This file is a progress note. How to run the project, the graph model, and the Cypher queries are in [README.md](README.md). The demo script is [DEMO.md](DEMO.md). The original plan is `actiongraph-track-01-complete-detailed-spec.html`.

## Where the build stands

| Plan day | Goal | Status |
| --- | --- | --- |
| Day 1 | FalkorDB, synthetic seed, reset, ownership evidence paths | Done |
| Day 2 | Plan schema, owner invariant, block/pass/needs-review, verdict API, edge ablation | Done |
| Day 3 | Approval, mock executor, idempotency, read-back verification, timeout and reconciliation | Done |
| Day 4 | Graph-suggested repair, MCP agent server, README, demo script | Done |
| Day 4 | Demo page, impact route, benchmark, injection and limit tests, event log and metrics, API Dockerfile | Done |
| Day 4 | Demo video, submission | In progress |

62 tests pass against FalkorDB `v4.22.1` on Python 3.9.6. A fresh clone from GitHub passes the README quick start.

**Live:** https://actiongraph.mannnan.com (AWS Lightsail, Mumbai; backup https://15-252-30-16.sslip.io). HTTPS, private FalkorDB, survives a reboot. Every commit to `main` that passes CI deploys itself through GitHub OIDC, with automatic rollback.

## Added on Day 4, beyond the original plan

- **Graph-suggested repair.** When a plan would orphan a critical service, a Cypher query finds active, eligible owners of another service that shares a critical dependency. The suggested plan is re-checked with the same evaluator before it is offered. It is never stored or run.
- **MCP server** (`python -m app.mcp_server`). Any MCP client, such as Claude Desktop, becomes the agent. It has seven tools and no approve tool: the agent can propose and execute, but only a person can approve, through `python -m app.cli approve`.
- **Second invariant node.** `INV-CRITICAL-DEPENDENCY` is now seeded with a description, and every Decision links to its invariant with `SUPPORTED_BY`.
- **CLI** `approve` and `reconcile` commands.
- **Demo page** at `/`: verdict, invariant report, evidence path, repair, blast radius, execution timeline, human approve button, reconcile, live edge ablation, JSON report export.
- **`GET /v1/plans/{id}/impact`**: bounded blast radius for every action target.
- **Benchmark** (`scripts/benchmark.py`): 100/100 unsafe plans stopped, 0/40 safe plans blocked, 20/20 ablation flips, preflight p50 about 16 ms.
- **Security tests**: injected graph or plan text cannot change a verdict; result limits truncate and say so; a state read at its cap fails closed.
- **Event log and `/v1/metrics`**: every spec event as a JSON line on stderr; counters and latency per process.
- **Dockerfile**: optional `api` service in `docker-compose.yml` (profile `app`), non-root, bound to `127.0.0.1`.

## Not built, on purpose

- A live LLM planner inside the server, raw Cypher from a model, real identity or cloud connectors, and production rollback claims.

## Left before submission

1. Try the agent in Claude Desktop once.
2. Record the video from DEMO.md.
3. Submit on Track 01 with the repo and video links.
