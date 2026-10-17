# ActionGraph

**A graph-native safety checkpoint for AI agents that change connected systems.** Before an agent runs a plan, ActionGraph reads the dependency graph in FalkorDB and decides PASS, BLOCK or NEEDS_REVIEW, with the exact path behind the decision. It runs approved plans one step at a time, and calls the result verified only when a read-back of the graph matches the postcondition.

**Hackathon:** Graph Hacks: Context for AI Agents. **Track 01: Agents That Act on Connected Data.**

## The problem

A contractor leaves. Someone asks an agent to "disable their account and revoke their access". The sentence sounds safe. The graph shows the contractor is the **only eligible owner** of a critical billing service. The other name on billing is a former employee whose account is already disabled. An agent that just does what it's told leaves billing with no owner.

ActionGraph:

1. **Blocks** that plan, naming the invariant and the path: `id-contractor-17 -[OWNS]-> svc-billing -[DEPENDS_ON]-> db-billing-primary`.
2. **Suggests a repair from the graph:** `id-engineer-9` already owns payments, which runs on the same critical database, so they can take billing.
3. **Re-checks the repair**, and flags that the contractor's deploy credential still serves the database payments uses.
4. **Waits for a human to approve.** The agent cannot approve its own plan.
5. **Executes one step at a time**, reading the graph back after each step. It reports `VERIFIED` only when the read-back says so.
6. **Is honest when something goes wrong.** A timeout gives `UNKNOWN`; a reconcile from the live graph gives `PARTIAL_FAILURE`. It never claims a rollback that didn't happen.

The verdict is computed in code from graph reads. No model decides policy.

## Architecture

```mermaid
flowchart LR
    U[Person] -->|prompt| A[MCP client / agent<br/>e.g. Claude Desktop]
    A -->|MCP stdio| M[app/mcp_server.py<br/>7 tools, no approve tool]
    U -->|approve| C[app/cli.py]
    H[HTTP client] --> F[app/main.py<br/>FastAPI]
    M --> S[Plan service<br/>validate + preflight]
    F --> S
    C --> S
    S --> V[Evaluator<br/>in-memory projection]
    S --> R[Repair<br/>suggest + re-check]
    S --> X[Runner<br/>approve, execute, reconcile]
    X --> AD[Mock adapter]
    X --> RB[Read-back]
    V & R & S & X & AD & RB <-->|Cypher| G[(FalkorDB<br/>graph: actiongraph)]
```

- **FalkorDB is the system of record.** It holds the workspace (identities, services, resources, credentials), the invariants, and the audit trail (plans, actions, decisions, approvals, executions, events, observations). The mock adapter writes its side effects there, and read-back reads them there.
- **Proposed changes are never written to the observed graph.** The evaluator copies the owner set, applies the plan in order in memory, and checks the invariants against that projection.
- **The graph changes the outcome.** Add one `OWNS` edge and a plan passes; delete it and the same plan blocks. That's `tests/test_graph_edge_ablation.py`.

## Graph data model

Graph name: `actiongraph`. All data is synthetic. No credential values are stored.

### Workspace (seeded by `python -m app.cli reset`)

| Label | Properties | Seed nodes |
| --- | --- | --- |
| `Workspace` | `id`, `name`, `schema_version`, `graph_version` | `ws-synthetic-1` (`seed-v1`) |
| `Identity` | `id`, `status`, `identity_type`, `eligible_owner`, `display_name` | `id-contractor-17` (active), `id-engineer-9` (active), `id-former-4` (disabled) |
| `Service` | `id`, `name`, `criticality`, `status` | `svc-billing` (critical), `svc-payments` (critical), `svc-handbook` (low) |
| `Resource` | `id`, `name`, `resource_type`, `criticality`, `status` | `db-billing-primary` (critical), `cache-billing` (low) |
| `Credential` | `id`, `status`, `scope`, `expires_at` | `credential-deploy-4` |
| `Invariant` | `id`, `rule_type`, `severity`, `enabled`, `description` | `INV-CRITICAL-SERVICE-OWNER`, `INV-CRITICAL-DEPENDENCY` |

| Relationship | Meaning |
| --- | --- |
| `(Identity)-[:OWNS]->(Service)` | Ownership. It counts only if the identity is `active` and `eligible_owner = true` |
| `(Service)-[:DEPENDS_ON]->(Resource)` | The service needs the resource. Only critical resources count as impact |
| `(Credential)-[:GRANTS_ACCESS_TO]->(Resource)` | Access path |
| `(Credential)-[:ISSUED_TO]->(Identity)` | Who holds the credential |
| `(Invariant)-[:PROTECTS]->(Service)` | Which rules apply to which services |
| `(Workspace)-[:HAS_IDENTITY / HAS_SERVICE / HAS_RESOURCE]->(…)` | Workspace membership |

Seeded edges: contractor and former employee `OWNS` billing; engineer `OWNS` payments and the handbook; billing and payments both `DEPENDS_ON` `db-billing-primary`; billing `DEPENDS_ON` `cache-billing`; the credential `GRANTS_ACCESS_TO` the database and is `ISSUED_TO` the contractor; both invariants `PROTECTS` billing and payments.

### Audit trail (written as plans run)

| Label | Written when | Links |
| --- | --- | --- |
| `Plan`, `Action` | A plan is created | `(Plan)-[:CONTAINS_ACTION]->(Action)-[:AFFECTS]->(target)` |
| `Decision` | Each preflight | `(Decision)-[:EVALUATES]->(Plan)`, `(Decision)-[:SUPPORTED_BY]->(Invariant)` |
| `Approval` | A person approves | `(Approval)-[:APPROVES]->(Plan)`, `(Approval)-[:APPROVED_BY]->(Identity)` |
| `Execution` | Execute starts | `(Plan)-[:HAS_EXECUTION]->(Execution)` |
| `ExecutionEvent`, `Observation` | After each step | `(Execution)-[:RECORDED]->(ExecutionEvent)-[:RESULT_OF]->(Action)`, `(Observation)-[:CONFIRMS]->(ExecutionEvent)` |

## Cypher the product depends on

All queries are parameterised and read-only unless noted. Each has a `LIMIT` and a timeout. Ids are checked against `^[A-Za-z0-9._-]{1,64}$` before any query runs.

**1. Critical services an identity owns** (`app/graph/queries.py`). This is the first hop of the evidence path.

```cypher
MATCH (i:Identity {id: $identity_id})-[rel:OWNS]->(s:Service)
WHERE s.criticality = 'critical' AND s.status = 'active'
RETURN i.id, i.status, i.eligible_owner, type(rel), s.id, s.criticality, s.status
ORDER BY s.id LIMIT $result_limit
```

**2. Co-owners, eligible or not** (`app/graph/queries.py`). This query shows that `id-former-4` does not save billing.

```cypher
MATCH (i:Identity {id: $identity_id})-[:OWNS]->(s:Service)<-[rel:OWNS]-(other:Identity)
WHERE s.criticality = 'critical' AND s.status = 'active' AND other.id <> $identity_id
RETURN other.id, other.status, other.eligible_owner, type(rel), s.id
ORDER BY s.id, other.id LIMIT $result_limit
```

**3. Three-hop credential path** (`app/graph/queries.py`): owner → service → critical resource ← credential issued to that owner.

```cypher
MATCH (i:Identity {id: $identity_id})-[:OWNS]->(s:Service)-[:DEPENDS_ON]->(r:Resource)
MATCH (c:Credential)-[grant:GRANTS_ACCESS_TO]->(r)
MATCH (c)-[issued:ISSUED_TO]->(i)
WHERE s.criticality = 'critical' AND s.status = 'active'
  AND r.criticality = 'critical' AND r.status = 'active'
RETURN i.id, s.id, r.id, c.id, c.status, c.scope, type(grant), type(issued)
ORDER BY c.id, r.id LIMIT $result_limit
```

**4. Observed state the verdict is computed from** (`app/graph/state.py`). Ownership, dependencies, access grants and invariants are read live on every preflight. If a read fails or hits its limit, there is no verdict. For example:

```cypher
MATCH (i:Identity)-[:OWNS]->(s:Service)
RETURN i.id AS identity_id, s.id AS service_id
ORDER BY i.id, s.id LIMIT $result_limit
```

**5. Replacement owners for an orphaned service** (`app/graph/queries.py`). This drives the graph-suggested repair: active, eligible owners of another live service that depends on the same critical resource.

```cypher
MATCH (orphan:Service {id: $service_id})-[:DEPENDS_ON]->(shared:Resource)
      <-[:DEPENDS_ON]-(peer:Service)<-[:OWNS]-(candidate:Identity)
WHERE shared.criticality = 'critical' AND shared.status = 'active'
  AND peer.id <> orphan.id AND peer.status = 'active'
  AND candidate.status = 'active' AND candidate.eligible_owner = true
  AND NOT candidate.id IN $excluded_ids
  AND NOT (candidate)-[:OWNS]->(orphan)
RETURN candidate.id, collect(DISTINCT peer.id), collect(DISTINCT shared.id),
       count(DISTINCT shared) AS shared_count
ORDER BY shared_count DESC, candidate.id LIMIT $result_limit
```

**6. Read-back after a step** (`app/execution/readback.py`). An adapter's "OK" is not evidence; this read is.

```cypher
MATCH (owner:Identity {id: $owner_id})
OPTIONAL MATCH (owner)-[rel:OWNS]->(service:Service {id: $service_id})
RETURN owner.status, owner.eligible_owner, count(rel) AS owns
```

**7. Mock side effect** (`app/execution/adapter.py`, a write). It runs only after approval, and only for allow-listed actions.

```cypher
MATCH (owner:Identity {id: $owner_id})
MATCH (service:Service {id: $service_id})
MERGE (owner)-[:OWNS]->(service)
```

### How a verdict is computed (`app/policy/evaluator.py`)

```text
for each action, in order:
    assign_service_owner  -> BLOCK if the new owner is not active and eligible,
                             otherwise add the edge to the in-memory projection
    disable_identity      -> for each critical service the identity owns:
                             eligible_after = current eligible owners
                                              - owners this plan disables
                                              + owners this plan adds earlier
                             BLOCK CRITICAL_SERVICE_WOULD_BE_ORPHANED if eligible_after is empty
    delete_resource       -> BLOCK if an active critical service depends on it
    revoke_credential     -> NEEDS_REVIEW if it grants access to a critical resource
                             that an active critical service depends on
stop at the first BLOCK; PASS only if nothing was found and the owner invariant is in the graph
```

Actions apply in order, so a replacement added *after* a disable doesn't excuse the disable. The same check runs again just before the first write, and again before each later step.

## Quick start

Tested on macOS with Docker Desktop, Python 3.9.6, and FalkorDB `v4.22.1`.

```bash
git clone https://github.com/Kartikey-Paliwal/Actiongraph.git
cd Actiongraph
docker compose up -d
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli health
python -m app.cli reset
python -m pytest -q
```

Expected: `falkordb: ok`, a seed summary with `graph_version: seed-v1`, and `37 passed`.

If FalkorDB is not running, the graph tests **skip** rather than fail, so check that pytest says `37 passed` with no skips.

The defaults work as they are. To change them, export these environment variables (`.env.example` lists them; the app does not load a `.env` file itself):

| Variable | Default | Meaning |
| --- | --- | --- |
| `FALKORDB_HOST` | `127.0.0.1` | Graph host |
| `FALKORDB_PORT` | `6379` | Graph port |
| `FALKORDB_GRAPH` | `actiongraph` | Graph name |
| `QUERY_TIMEOUT_MS` | `2000` | Per-query timeout |
| `RESULT_LIMIT` | `100` | Row cap for impact queries |

Ports are bound to `127.0.0.1` only. The FalkorDB browser is at `http://localhost:3000`; connect to `localhost:6379` and pick graph `actiongraph`.

## Run the demo

The full script with narration is in [DEMO.md](DEMO.md). Run `reset` before each run.

```bash
python -m app.cli impact --identity id-contractor-17
python -m app.cli preflight --fixture unsafe
python -m app.cli reset
python -m app.cli run --fixture safe
python -m app.cli reset
python -m app.cli run --fixture safe --inject timeout_unknown
python -m app.cli reconcile --execution "<execution_id from the previous output>"
python -m pytest tests/test_graph_edge_ablation.py -v
```

| Command | Result |
| --- | --- |
| `preflight --fixture unsafe` | `BLOCK` / `CRITICAL_SERVICE_WOULD_BE_ORPHANED`, the evidence path, and `suggested_repair` naming `id-engineer-9` |
| `run --fixture safe` | Preflight `PASS`, then approve, then execute. Result: `VERIFIED` from read-back |
| `run --fixture safe --inject timeout_unknown` | `UNKNOWN`, review required |
| `reconcile --execution …` | `PARTIAL_FAILURE`: the owner was assigned and the contractor is still active |

### Use it as an agent over MCP

`app/mcp_server.py` is a stdio MCP server, so any MCP client can act as the agent. It's written without the MCP SDK, because the SDK needs Python 3.10 and this project pins 3.9.

| Tool | What it does |
| --- | --- |
| `list_workspace` | Identities, services with their owners, resources, credentials |
| `impact` | Critical ownership and evidence paths for an identity |
| `propose_plan` | Stores a plan and preflights it. Returns the verdict, evidence and `suggested_repair` |
| `get_plan_report` | Status, verdict, approval and execution steps |
| `request_approval` | Returns the command a **person** runs to approve. Writes nothing |
| `execute_plan` | Runs an approved plan with read-back. Refused without a human approval |
| `reconcile_execution` | Re-reads the graph after an `UNKNOWN` |

There is no approve tool. A person approves in a terminal:

```bash
python -m app.cli approve --plan "<plan_id>" --approver id-engineer-9 --reason "Engineer 9 takes billing"
```

**Claude Desktop setup:** add this to `claude_desktop_config.json`, using the absolute path of your clone, then restart the app:

```json
{
  "mcpServers": {
    "actiongraph": {
      "command": "/absolute/path/to/Actiongraph/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "env": { "PYTHONPATH": "/absolute/path/to/Actiongraph" }
    }
  }
}
```

Then try: *"Contractor id-contractor-17 is leaving today. Disable their account and revoke their deploy credential."*

### HTTP API

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

| Method | Path | Behaviour |
| --- | --- | --- |
| GET | `/health` | 503 if FalkorDB is down |
| POST | `/v1/plans` | Validate and store a plan. 201, 422 (unsupported action or unknown target), 409 or 503 |
| POST | `/v1/plans/{id}/preflight` | Verdict and evidence, plus `suggested_repair` on an orphaning block |
| POST | `/v1/plans/{id}/approve` | `{approver_id, reason}`. Only from `AWAITING_APPROVAL` |
| POST | `/v1/plans/{id}/execute` | `{idempotency_key, failure_injection?}`. Only from `APPROVED` |
| POST | `/v1/executions/{id}/reconcile` | Re-read state after `UNKNOWN` |
| GET | `/v1/plans/{id}` | Plan, actions and latest verdict |
| GET | `/v1/plans/{id}/report` | Audit report: plan, verdict, approval, execution |

## Plan states

`PROPOSED → VALIDATING → BLOCKED | NEEDS_REVIEW | AWAITING_APPROVAL → APPROVED → EXECUTING → VERIFIED | PARTIAL_FAILURE | FAILED | UNKNOWN`

- **VERIFIED:** every step's postcondition, and the final invariant, read back true.
- **UNKNOWN:** a step timed out and its read-back also failed. Review required.
- **PARTIAL_FAILURE:** at least one step verified, and a later one is established as not done.
- **FAILED:** the first step is established as not done.

A timeout with unchanged state is retried once, because these actions are idempotent. Running the same `idempotency_key` again returns the stored result without applying anything twice.

## Tests

37 tests in `tests/`. They need FalkorDB running.

| File | What it proves |
| --- | --- |
| `test_ownership_path.py` | The contractor owns billing; the low-criticality cache and handbook are ignored; the disabled co-owner does not count; reset restores a deleted edge; bad ids are rejected |
| `test_unsafe_plan_blocked.py` | The unsafe offboard blocks with the owner path and does not execute |
| `test_safe_plan_passes.py` | The safe handoff waits for approval; an ineligible replacement blocks; deleting the shared database names both services and the dependency invariant |
| `test_graph_edge_ablation.py` | **Same plan, same code: add an `OWNS` edge → PASS; delete that edge → BLOCK** |
| `test_repair_suggestion.py` | The repair names `id-engineer-9` with its path; the re-check is honest; suggesting writes nothing; with no eligible candidate in the graph, no repair is offered |
| `test_execution.py` | A blocked plan never runs; no execute before approval; read-back decides `VERIFIED`; running twice applies once; a graph change after approval blocks before any write; timeout → retry, `UNKNOWN`, `PARTIAL_FAILURE`; an adapter "success" without a write is not verified |
| `test_fail_closed.py` | Unsupported actions store nothing; unknown targets are rejected; a dead graph gives no verdict |
| `test_mcp_server.py` | The protocol handshake; there's no approve tool; the agent flow needs a human approval; bad arguments are rejected; a dead graph gives an error, not a verdict; stdout stays clean JSON-RPC |

## Limitations

This is a bounded prototype for four action types (`assign_service_owner`, `disable_identity`, `revoke_credential`, `delete_resource`) in one synthetic workspace. "Verified" means the read-back matched the postcondition.

- Side effects are simulated in the graph. There are no real identity or cloud connectors, and no production rollback.
- Two invariants. The final verification after a run is specific to this scenario.
- The repair suggests one replacement for the first orphaned service it finds.
- No authentication on the API. It is meant to run on localhost.
- The agent's path in an MCP client can vary between runs; the verdicts don't.

## AI assistance disclosure

AI coding assistants (Cursor and Claude Code) were used to help write code, tests and documentation. I designed the scenario and the graph model, reviewed every change, ran the tests, and can explain the graph model, every query above, the verdict logic, and each failure state.

## Project layout

```text
app/cli.py                  health, reset, impact, preflight, run, approve, reconcile
app/main.py                 FastAPI routes
app/mcp_server.py           MCP stdio server for agents
app/graph/                  client, seed, impact and repair queries, observed-state load
app/planning/               allow-listed plan schema, demo fixtures
app/policy/evaluator.py     in-memory projection and invariant verdict
app/policy/repair.py        graph-suggested repair and its re-check
app/plans/                  plan service, Plan/Action/Decision storage
app/execution/              approval, runner, mock adapter, read-back, journal
tests/                      37 tests
docker-compose.yml          falkordb/falkordb:v4.22.1, bound to 127.0.0.1
DEMO.md                     demo script with commands and narration
```

Pinned versions: FalkorDB image `v4.22.1`, Python client `FalkorDB==1.2.2`, FastAPI `0.115.6`, pytest `8.4.2`.
