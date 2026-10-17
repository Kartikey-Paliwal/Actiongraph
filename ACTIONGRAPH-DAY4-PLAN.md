# ActionGraph — Day 4 plan

Written 16 October 2026, evening Pacific time. Submissions close 18 October 2026, 11:59 PM IST. About a day and a half remains.

Days 1–3 are done: the FalkorDB seed, the block/pass verdict, approval, mock execution, read-back verification, and the honest timeout. Day 4 does not add a new safety idea. It makes the contractor story visible, repeatable, and submittable.

Track: 01, Agents That Act on Connected Data.

## What “done” means

A judge who has never seen the repo can do all of this:

1. Read one sentence of the problem in the README.
2. Start FalkorDB and the app with the documented commands.
3. Click three buttons, or run three CLI commands, and see the graph change the outcome.
4. Watch a two-minute video if they do not run it themselves.
5. See the ablation: the same disable plan passes with a second owner edge and blocks after that edge is removed.
6. Read the limitations. The project claims a bounded prototype, not universal safety.

If time runs out, the video, the README, and the passing tests are more important than a polished screen.

## The story the demo tells

A contractor has left the company. Someone asks the agent to disable their account and revoke their access.

The graph, not the sentence, decides what is safe.

Contractor `id-contractor-17` is the only eligible owner of critical service `svc-billing`. That service depends on database `db-billing-primary`. Credential `credential-deploy-4` is issued to the contractor and grants access to that database. Disabled co-owner `id-former-4` still has an `OWNS` edge and does not count. Engineer `id-engineer-9` is active and eligible, and owns `svc-payments`, which shares the same database. They do not yet own billing.

Beat A, about 25 seconds. The naive plan disables the contractor and revokes the credential. ActionGraph returns `BLOCK` / `CRITICAL_SERVICE_WOULD_BE_ORPHANED`. The screen shows the path `id-contractor-17 -[OWNS]-> svc-billing -[DEPENDS_ON]-> db-billing-primary`, and shows that `id-former-4` does not save the service. No execution starts.

Beat B, about 40 seconds. The safe plan assigns `id-engineer-9` to billing first, then disables the contractor. Preflight returns `PASS` and `AWAITING_APPROVAL`. The engineer approves. The executor writes the ownership edge, reads it back, and only then disables the contractor. Status becomes `VERIFIED` only because read-back shows the engineer as the eligible owner and the contractor as disabled.

Beat C, about 25 seconds. Reset, run the safe plan again, and inject a timeout on disable whose read-back also fails. Status is `UNKNOWN`, with review required. Reconcile reads the live graph and reports a partial failure: ownership was assigned, the disable is not verified. Do not say the step was rolled back.

Beat D, about 20 seconds. Show the ablation test, or a button that adds the engineer as a second owner, runs disable, then deletes that edge and runs the same plan again. Pass, then block. That is the proof FalkorDB is doing the work.

Close on the test command and the one-sentence limitation.

## Build order

Do these in order. Stop at the end of a step if the clock is short. Do not start a later step by skipping the video and README.

### 1. Freeze the demo script

Write `DEMO.md` with the four beats, the exact commands, and the sentence to say on each beat. Rehearse once before recording. The script is the source for the UI labels and the video.

Commands that already exist:

```bash
source .venv/bin/activate
python -m app.cli reset
python -m app.cli preflight --fixture unsafe
python -m app.cli run --fixture safe
python -m app.cli reset
python -m app.cli run --fixture safe --inject timeout_unknown
python -m pytest tests/test_graph_edge_ablation.py -q
```

`reset` before every safe run. A second safe run on an already-verified graph is a different story.

### 2. One demo screen, not a product

Serve a single page from FastAPI. No login, no node editor, no second scenario.

The page has a reset control and three actions: Block unsafe offboard, Run safe handoff, Inject unknown timeout. After each action it shows:

- Verdict word and reason code, with text as well as color.
- Invariant id and the plain sentence already stored on the invariant node.
- Evidence path as a list of steps, including the ineligible co-owner.
- Execution timeline: one row per action with adapter outcome, read-back, and whether the postcondition was verified.
- Final status: `BLOCKED`, `VERIFIED`, `UNKNOWN`, or `PARTIAL_FAILURE`.
- A line that says execution did not start when the plan is blocked.

A fourth control, or a short note under the timeline, points at the ablation test command. Building a live “delete this edge” button is worth it only after the three beats work. The test is already the proof.

Keep the page calling the existing API: create, preflight, approve, execute, reconcile, report. The safe-run flow can be one backend route, `POST /v1/demo/safe`, that performs reset-safe steps the CLI already does, so the UI does not reimplement policy in JavaScript. The browser must not decide the verdict.

Add a reset route that calls the existing seed reset and returns the node counts.

Check the page in the browser: click all three actions, confirm the blocked plan has no timeline of writes, confirm the safe plan shows read-back before `VERIFIED`, and confirm the timeout path says `UNKNOWN`.

### 3. README that a stranger can run

Replace guesswork with commands that were just run from a clean shell. Include:

- One sentence: who it is for, and the contractor problem.
- Track 01.
- Architecture in a short paragraph plus the graph labels and the relationships that change a verdict.
- The Cypher idea in plain language: eligible owners are active identities with `eligible_owner` and an `OWNS` edge. The verdict is computed in code from that read. Proposed changes are not written until an approved execute.
- Setup: Docker, `docker compose up -d`, venv, `pip install -r requirements.txt`, env vars from `.env.example`.
- `health`, `reset`, the three demo commands, `pytest`, and the uvicorn command bound to `127.0.0.1`.
- FalkorDB browser at `http://localhost:3000`, with a warning that the graph port stays on localhost.
- Test count and what the ablation test proves.
- Limitations: synthetic data, four action types, mock side effects, no production rollback, no live LLM planner.
- AI assistant disclosure: assistants were used, and the author can explain the queries, the verdict, and the failure states.
- Pinned versions: FalkorDB image `v4.22.1`, Python client `1.2.2` on Python 3.9.

Do not leave placeholder commands in the README.

### 4. Record the video

Two minutes, one take if possible. Order: danger, block with path, safe run with read-back, unknown timeout, ablation or the test command, limitation sentence.

Show the UI if it works. If the UI is late, record the CLI and the FalkorDB browser. A true recording of the working project beats a slide.

Say this limitation out loud: “This is a bounded prototype for these actions in a synthetic workspace. Verified means the read-back matched the postcondition.”

### 5. Fresh-clone check

From another directory, follow only the README. Start the containers, install, reset, run unsafe, run safe, run pytest. Fix whatever the README got wrong. This is the check that usually fails at the deadline.

### 6. Submit

Public repo. Track 01 selected. Link the video. If a public URL is unstable, say the project runs locally and point at the README. Do not call a local demo a live deployment.

Submit with time left for the form to fail.

## Leave out

- A live LLM that writes the plan. The fixtures are the demo.
- A force-directed graph. A path list is the evidence.
- More invariants, more companies, fraud, or a codebase scenario.
- Real identity systems, real credentials, or a public FalkorDB port.
- Shortest-path as a second copy of the same evidence.
- Rewriting the verdict engine while polishing the page.

The AirPods blog is optional and only after the repo and video are submitted. One post: the contractor plan looked fine, the ownership path showed it would orphan billing, and API success was not treated as verification.

## Schedule

| Block | Work | Stop when |
| --- | --- | --- |
| First | Demo script and UI wired to the existing API | Three clicks show block, verified, and unknown |
| Second | README and fresh-clone run | A new shell can reset, run both fixtures, and pass pytest |
| Third | Video, ablation mention, limitations, disclosure | The two-minute file exists |
| Last | Submit Track 01 | The form is in before 11:59 PM IST on 18 October |

## Submission checklist

- FalkorDB is the graph the verdict and the read-back use.
- The ablation test is named in the README.
- Unsafe plans do not execute. Safe plans execute only after approval.
- Timeout and partial failure are visible.
- Seed data is synthetic. No credential values are stored.
- README, demo video, and local run instructions are complete.
- AI use is disclosed.
- Track 01 is selected on the form.
