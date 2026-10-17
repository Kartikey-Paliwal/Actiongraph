# ActionGraph — work done so far

Status date: 15 October 2026, evening Pacific time (16 October 2026, morning India time).

Hackathon: [Graph Hacks: Context for AI Agents](https://www.wemakedevs.org/hackathons/falkordb), Track 01, Agents That Act on Connected Data. Submissions close 18 October 2026, 11:59 PM IST.

This file records what is built and checked. It is a progress note, not the product specification. The full plan remains in `actiongraph-track-01-complete-detailed-spec.html`.

## Where the build stands

Days 1 and 2 of the four-day plan are done. The graph is real, and a proposed plan gets a deterministic verdict from that graph. Execution has not started.

| Plan day | Goal | Status |
| --- | --- | --- |
| Day 1 | FalkorDB, synthetic seed, reset, ownership evidence paths | Done |
| Day 2 | Plan schema, owner invariant, block/pass/needs-review, verdict API, edge ablation | Done |
| Day 3 | Approval, mock executor, read-back verification, timeout and reconciliation | Not started |
| Day 4 | UI, README, demo video, deployment packaging, submission | Not started |

Fifteen tests passed against a local FalkorDB `v4.22.1` container on 15 October 2026.

## What a judge can see today

1. Contractor `id-contractor-17` owns critical service `svc-billing`, which depends on `db-billing-primary`. Credential `credential-deploy-4` grants access to that database and is issued to the contractor.
2. Disabled co-owner `id-former-4` also has an `OWNS` edge. The impact result shows that edge and marks it as not eligible.
3. A low-criticality cache dependency is returned separately and is not treated as critical impact.
4. Engineer `id-engineer-9` owns critical service `svc-payments`, which shares `db-billing-primary`.
5. Invariant node `INV-CRITICAL-SERVICE-OWNER` protects both critical services. Workspace graph version is `seed-v1`.
6. Disabling the contractor returns `BLOCK` / `CRITICAL_SERVICE_WOULD_BE_ORPHANED`. No action runs.
7. Assigning the engineer as billing owner first, then disabling the contractor, returns `PASS` with status `AWAITING_APPROVAL`.
8. The same disable plan passes while the engineer also owns billing, and blocks again after that ownership edge is deleted.
9. Deleting `db-billing-primary` blocks and names both `svc-billing` and `svc-payments`.

The verdict is computed in code from a live graph read. The model does not decide policy. Proposed ownership is projected in memory. The observed graph is not rewritten to pretend a plan already happened.

## Graph that is seeded

Graph name: `actiongraph`. Reset deletes that graph and loads the same scenario again.

| Label | Id | Role |
| --- | --- | --- |
| Workspace | `ws-synthetic-1` | `graph_version` `seed-v1` |
| Identity | `id-contractor-17` | Active, eligible, owns billing |
| Identity | `id-engineer-9` | Active, eligible, owns payments and the handbook |
| Identity | `id-former-4` | Disabled, not eligible, still has an `OWNS` edge to billing |
| Service | `svc-billing` | Critical |
| Service | `svc-payments` | Critical, shares the billing database |
| Service | `svc-handbook` | Low criticality |
| Resource | `db-billing-primary` | Critical database |
| Resource | `cache-billing` | Low-criticality cache used by billing |
| Credential | `credential-deploy-4` | Deploy scope. No secret value is stored |
| Invariant | `INV-CRITICAL-SERVICE-OWNER` | Protects billing and payments |

Relationships: `HAS_*`, `OWNS`, `DEPENDS_ON`, `GRANTS_ACCESS_TO`, `ISSUED_TO`, `PROTECTS`.

Plans and decisions are also stored in this graph after a preflight: `Plan`, `Action`, `Decision`, with `CONTAINS_ACTION`, `AFFECTS`, `EVALUATES`, and `SUPPORTED_BY`.

## Verdict behavior

Allow-listed actions: `assign_service_owner`, `disable_identity`, `revoke_credential`, `delete_resource`. Anything else is rejected with HTTP 422 before a plan node is written. Unknown targets are rejected the same way. If FalkorDB cannot be reached, plan creation fails and does not return a pass.

Actions are applied in order. A later replacement cannot excuse an earlier disable that would leave a critical service with zero eligible owners.

| Plan | Result |
| --- | --- |
| Disable `id-contractor-17` and revoke `credential-deploy-4` | `BLOCK`, billing would have no eligible owner. Revoke is not reached |
| Assign `id-engineer-9` on `svc-billing`, then disable the contractor | `PASS`, status `AWAITING_APPROVAL`. Eligible owner afterward is the engineer |
| Assign disabled `id-former-4`, then disable the contractor | `BLOCK`, `REPLACEMENT_NOT_ELIGIBLE` |
| Delete `db-billing-primary` | `BLOCK`, `CRITICAL_RESOURCE_HAS_ACTIVE_DEPENDENTS`, billing and payments |
| Disable the contractor while the engineer also owns billing | `PASS` |
| Same disable after that engineer ownership edge is removed | `BLOCK` |

High-risk actions keep `requires_approval` true. `execution_started` is false on every current verdict because there is no executor yet.

## API and commands

FalkorDB is bound to localhost only: graph port `6379`, browser `http://localhost:3000`.

```bash
source .venv/bin/activate
python -m app.cli health
python -m app.cli reset
python -m app.cli impact --identity id-contractor-17
python -m app.cli preflight --fixture unsafe
python -m app.cli preflight --fixture safe
python -m app.cli preflight --fixture delete-resource
python -m pytest
```

API, on `127.0.0.1:8000`:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Pings FalkorDB. Returns 503 if the graph is down |
| POST | `/v1/plans` | Validates and stores a plan. 201, 422, or 503 |
| POST | `/v1/plans/{id}/preflight` | Reads the live graph and stores a decision |
| GET | `/v1/plans/{id}` | Plan, actions, and latest verdict |

These spec routes are not built yet: approve, execute, impact-by-plan, reconcile, and audit report.

## Tests

Fifteen tests, all passing when FalkorDB is up:

| File | What it proves |
| --- | --- |
| `tests/test_ownership_path.py` | Contractor owns billing, cache is ignored, handbook is not critical impact, engineer owns payments, disabled co-owner does not count, reset restores a deleted ownership edge, bad ids are rejected |
| `tests/test_unsafe_plan_blocked.py` | Unsafe offboard blocks with the owner path and does not execute |
| `tests/test_safe_plan_passes.py` | Safe handoff passes and waits for approval. Ineligible replacement blocks. Shared-database delete names both services |
| `tests/test_graph_edge_ablation.py` | Adding a second owner makes disable pass. Removing that edge makes the same plan block |
| `tests/test_fail_closed.py` | Unsupported actions write no plan. Unknown targets are rejected. A closed graph port does not produce a verdict |

## Code layout

```text
app/cli.py                  health, reset, impact, preflight
app/main.py                 FastAPI routes
app/config.py               host, port, graph name, query limits
app/errors.py
app/graph/client.py         FalkorDB connection
app/graph/seed.py           synthetic scenario and reset
app/graph/queries.py        bounded impact paths
app/graph/state.py          observed-state load used by preflight
app/planning/validator.py   allow-listed plan schema
app/planning/fixtures.py    unsafe, safe, and delete-resource plans
app/policy/evaluator.py     projection and invariant verdict
app/plans/store.py          Plan, Action, and Decision nodes
app/plans/service.py        create and preflight
docker-compose.yml          falkordb/falkordb:v4.22.1
requirements.txt            FalkorDB 1.2.2, FastAPI 0.115.6, pytest 8.4.2
```

Python is the system 3.9 interpreter in `.venv`. The FalkorDB Python client is pinned to 1.2.2 because that is the newest release that installs on 3.9. The server image is pinned to `v4.22.1`.

## Not built yet

Day 3, from the spec:

- Approval record and an execute call that refuses to run until that record exists
- Mock executor for the allow-listed actions, one step at a time
- Idempotency keys so a repeated execute does not apply twice
- Read-back after each step. Adapter success is not verification
- Timeout handling: read state back, retry only when the action is idempotent and the state is unchanged, otherwise `UNKNOWN` and human review
- Re-preflight immediately before the first side effect
- Execution events and observations stored in FalkorDB

Day 4, from the spec:

- The four demo screens: verdict, blast-radius paths, execution timeline, invariant report
- README with commands verified from a clean clone
- Architecture diagram, query notes, limitations, and AI-assistant disclosure
- Demo video of about two minutes
- Public repo and Track 01 named on the submission
- Live URL or complete local run instructions

Also still out of scope, on purpose: a live LLM planner, raw Cypher from a model, real identity or cloud connectors, production rollback claims, and shortest-path as a second copy of the evidence path.

## How to continue

Next build is Day 3. The safe plan should move from `AWAITING_APPROVAL` to a mock run only after an explicit approval, then read the simulated graph back and mark the plan verified only if billing is owned by `id-engineer-9` and the contractor is disabled. The demo failure is a timeout on the disable step, with an honest `UNKNOWN` when read-back also fails.
