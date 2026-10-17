# ActionGraph — demo script

Track 01, Agents That Act on Connected Data. Target length: about two and a half minutes.

Recording order: the agent run (it covers the block, the repair, and the verified handoff), then Beat C (timeout), Beat D (ablation), and the close. The opening and Beats A and B are the CLI fallback for the agent run.

This script is the source for the demo page labels, the README demo section, and the video narration. Every command below was run on 17 October 2026 against FalkorDB `v4.22.1`, and the outputs quoted here are from that run.

## Before recording

```bash
docker compose up -d
source .venv/bin/activate
python -m app.cli health
python -m app.cli reset
```

Expected: `falkordb: ok`, then `graph_version: seed-v1` with 3 identities, 3 services, 2 resources, 1 credential, 2 invariants, 1 workspace.

Run `reset` before every safe run. A second safe run on a graph that is already verified tells a different story.

Optional second window: the FalkorDB browser at `http://localhost:3000`, graph `actiongraph`.

## Agent run — the headline (about 75 seconds)

An MCP client is the agent. ActionGraph is its MCP server, with seven tools: `list_workspace`, `impact`, `propose_plan`, `get_plan_report`, `request_approval`, `execute_plan`, `reconcile_execution`. There is no approve tool. The agent can propose and execute, but only a person can approve.

### One-time setup (Claude Desktop)

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "actiongraph": {
      "command": "/Users/kartikeypaliwal/Desktop/Action graph/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "env": { "PYTHONPATH": "/Users/kartikeypaliwal/Desktop/Action graph" }
    }
  }
}
```

FalkorDB must be running. Run `python -m app.cli reset` before each take.

### Prompt to type

> Contractor id-contractor-17 is leaving today. Disable their account and revoke their deploy credential.

### What should happen

1. The agent calls `impact` or `list_workspace`, then `propose_plan` with disable + revoke. The verdict is **BLOCK** / `CRITICAL_SERVICE_WOULD_BE_ORPHANED`, and `suggested_repair` names `id-engineer-9` with its graph path.
2. The agent proposes the suggested repair (assign, disable, revoke). The verdict is **NEEDS_REVIEW** / `CREDENTIAL_SERVES_CRITICAL_RESOURCE`, because the deploy credential still serves the database payments uses.
3. The agent splits the revoke out and proposes assign + disable. The verdict is **PASS** / `AWAITING_APPROVAL`.
4. The agent calls `request_approval` and shows a command like `python -m app.cli approve --plan <id> --approver id-engineer-9 --reason "..."`.
5. **Switch to the terminal and run that command.** Then tell the agent "Approved."
6. The agent calls `execute_plan`. The result is **VERIFIED**, with read-back showing `id-engineer-9` owns billing and the contractor is disabled.

If the agent tries `execute_plan` before step 5, it gets an error saying execute requires APPROVED. That's a good moment to keep on camera.

Say:

> "Here the agent is Claude, talking to ActionGraph over MCP. It asks for exactly what I asked for, and the graph blocks it: billing would lose its only owner. The graph also suggests who could take over, the engineer who already runs payments on the same database. The agent's next try flags the credential, so it splits that step out for review. Now the plan passes, but the agent can't approve its own plan. I do that, as a person. Then it runs, and it's marked verified only because the graph read-back says so."

The CLI beats below tell the same story step by step. Use them if a live agent take goes wrong.

## Opening — the danger (about 15 seconds)

```bash
python -m app.cli impact --identity id-contractor-17
```

What to point at:

- `id-contractor-17 -[OWNS]-> svc-billing -[DEPENDS_ON]-> db-billing-primary`
- `id-former-4 -[OWNS]-> svc-billing` with `counts_as_eligible_owner=false`
- `cache-billing` listed under ignored dependencies, because it is not critical

Say:

> "A contractor is leaving. Someone asks an agent to disable their account and revoke their access. The sentence sounds safe. The graph says this contractor is the only eligible owner of billing, a critical service. The other name on billing is a former employee whose account is already disabled, so they don't count."

## Beat A — the unsafe plan is blocked (about 25 seconds)

```bash
python -m app.cli preflight --fixture unsafe
```

Expected:

- `"status": "BLOCKED"`, `"verdict": "BLOCK"`
- `"reason_code": "CRITICAL_SERVICE_WOULD_BE_ORPHANED"`
- `"invariant"`: `INV-CRITICAL-SERVICE-OWNER`, "Every active critical service has at least one eligible active owner"
- `"current_eligible_owners": ["id-contractor-17"]`, `"eligible_after": []`, `"ineligible_owners": ["id-former-4"]`
- `"graph_path"` lists the three edges
- `"execution_started": false`
- `"suggested_repair"`:
  - candidate `id-engineer-9`, with `evidence_path`: `id-engineer-9 -[OWNS]-> svc-payments`, `svc-payments -[DEPENDS_ON]-> db-billing-primary`, `svc-billing -[DEPENDS_ON]-> db-billing-primary`
  - `actions`: assign `id-engineer-9` to billing, then disable, then revoke
  - `preflight`: `NEEDS_REVIEW` / `CREDENTIAL_SERVES_CRITICAL_RESOURCE`
  - `applied: false`

Say:

> "The naive plan disables the contractor and revokes the credential. ActionGraph reads the live ownership graph in FalkorDB and projects the plan in memory. Billing would end up with zero eligible owners, so the plan is blocked. Here is the exact path, and here is the named invariant. The verdict is computed in code from the graph, not by a model. Execution never started."
>
> "And the graph doesn't just say no. It finds who could take over: the engineer already runs payments on the same database. It re-checks that fix too, and flags that the deploy credential still serves payments, so that step needs review. Nothing is applied until someone submits and approves a plan."

## Beat B — the safe plan runs and is verified (about 40 seconds)

```bash
python -m app.cli reset
python -m app.cli run --fixture safe
```

This one command does what the API does in four calls: create, preflight (`PASS`, `AWAITING_APPROVAL`), approve as `id-engineer-9`, then execute.

Expected:

- `"status": "VERIFIED"`
- Step 1 `assign_service_owner`: `adapter_outcome: accepted`, read-back `owns: true`, `owner_status: active`, `postcondition_verified: true`
- Step 2 `disable_identity`: read-back `status: disabled`, `postcondition_verified: true`
- `"verification"`: `eligible_owners: ["id-engineer-9"]`, `contractor_status: disabled`, `invariant_holds: true`

Say:

> "The safe plan assigns the engineer as billing owner first, then disables the contractor. Preflight passes, but it still waits for an explicit approval. Right before the first write, ActionGraph checks the graph again. The executor writes the ownership edge and reads it back. Only after that does it disable the contractor. The status is VERIFIED because the read-back shows the engineer as the eligible owner and the contractor as disabled, not because the adapter said OK."

## Beat C — a timeout gives an honest UNKNOWN (about 25 seconds)

```bash
python -m app.cli reset
python -m app.cli run --fixture safe --inject timeout_unknown | tee /tmp/ag-unknown.json
```

Expected:

- `"status": "UNKNOWN"`, `"review_required": true`
- Step 1 `assign_service_owner`: `postcondition_verified: true`
- Step 2 `disable_identity`: `adapter_outcome: timeout`, `readback: null`, `note: injected read-back failure`

Then reconcile from the live graph, pasting the `execution_id` from the output above:

```bash
python -m app.cli reconcile --execution "<execution_id>"
```

Expected:

- `"status": "PARTIAL_FAILURE"`
- Step 1 read-back: engineer owns billing, `postcondition_verified: true`
- Step 2 read-back: `status: active`, `postcondition_verified: false`
- `"verification"`: `contractor_status: active`, `invariant_holds: false`

Say:

> "Now the disable call times out, and the read-back fails too. A timeout doesn't mean the action failed. It means we don't know. So the run is UNKNOWN and needs a human. Reconcile reads the live graph: the ownership change happened, and the contractor is still active. That is a partial failure. Nothing was rolled back, and we don't claim it was."

## Beat D — the graph is doing the work (about 20 seconds)

```bash
python -m pytest tests/test_graph_edge_ablation.py -v
```

Expected: `test_removing_the_second_owner_edge_flips_pass_to_block PASSED`.

What the test does: it adds the edge `id-engineer-9 -[OWNS]-> svc-billing` and preflights "disable the contractor", which gives `PASS`. Then it deletes only that edge and preflights the same plan again, which gives `BLOCK` / `CRITICAL_SERVICE_WOULD_BE_ORPHANED`.

Say:

> "Same plan, same code. Add one ownership edge and it passes. Delete that edge and it blocks. The verdict follows the graph."

## Close (about 15 seconds)

```bash
python -m pytest -q
```

Expected: `37 passed`. If FalkorDB is down, the graph tests skip instead of failing, so check that the line says `37 passed` with no skips.

Say:

> "Thirty-seven tests cover the block, the graph-suggested repair, the MCP agent tools, the safe run, the timeouts, a run asked for twice, fail-closed behaviour, and the edge ablation. This is a bounded prototype for these actions in a synthetic workspace. Verified means the read-back matched the postcondition."

## Notes for the recording

- Keep the terminal font large. Scroll to the field you are talking about. Don't read the whole JSON.
- Use one take if possible. If a beat goes wrong, run `reset` and redo that beat only.
- All data is synthetic. No credential values are stored in the graph.
