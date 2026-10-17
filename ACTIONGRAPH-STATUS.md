# ActionGraph — status

Status date: 17 October 2026, Pacific time.

Hackathon: [Graph Hacks: Context for AI Agents](https://www.wemakedevs.org/hackathons/falkordb), Track 01, Agents That Act on Connected Data. Submissions close 18 October 2026, 11:59 PM IST.

This file is a progress note. How to run the project, the graph model, and the Cypher queries are in [README.md](README.md). The demo script is [DEMO.md](DEMO.md). The original plan is `actiongraph-track-01-complete-detailed-spec.html`.

## Where the build stands

| Plan day | Goal | Status |
| --- | --- | --- |
| Day 1 | FalkorDB, synthetic seed, reset, ownership evidence paths | Done |
| Day 2 | Plan schema, owner invariant, block/pass/needs-review, verdict API, edge ablation | Done |
| Day 3 | Approval, mock executor, idempotency, read-back verification, timeout and reconciliation | Done |
| Day 4 | Graph-suggested repair, MCP agent server, README, demo script | Done |
| Day 4 | Demo video, fresh-clone check, submission | In progress |

37 tests pass against FalkorDB `v4.22.1` on Python 3.9.6.

## Added on Day 4, beyond the original plan

- **Graph-suggested repair.** When a plan would orphan a critical service, a Cypher query finds active, eligible owners of another service that shares a critical dependency. The suggested plan is re-checked with the same evaluator before it is offered. It is never stored or run.
- **MCP server** (`python -m app.mcp_server`). Any MCP client, such as Claude Desktop, becomes the agent. It has seven tools and no approve tool: the agent can propose and execute, but only a person can approve, through `python -m app.cli approve`.
- **Second invariant node.** `INV-CRITICAL-DEPENDENCY` is now seeded with a description, and every Decision links to its invariant with `SUPPORTED_BY`.
- **CLI** `approve` and `reconcile` commands.

## Not built, on purpose

- A web UI. The agent chat, the CLI, and the FalkorDB browser carry the demo.
- A Dockerfile for the API. FalkorDB runs in Docker; the app runs from a local venv.
- `GET /v1/plans/{id}/impact`. The `impact` CLI command and MCP tool cover it.
- A live LLM planner inside the server, raw Cypher from a model, real identity or cloud connectors, and production rollback claims.

## Left before submission

1. Record the video from DEMO.md.
2. Commit and push, then check a fresh clone against the README.
3. Submit on Track 01 with the repo and video links.
