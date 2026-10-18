"""HTTP API for plan creation and graph preflight, plus the local demo page."""

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import demo
from app.errors import ExecutionRejected, GraphUnavailable, PlanExists, PlanNotFound, PlanValidationError
from app.execution.runner import approve_plan, build_report, execute_plan, reconcile_execution
from app.graph.client import ping
from app.observability import snapshot
from app.plans.service import create_plan, plan_impact, preflight_plan
from app.plans.store import get_plan


_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


class ActionIn(BaseModel):
    type: str
    target_id: str
    params: dict = Field(default_factory=dict)
    preconditions: list = Field(default_factory=list)
    postconditions: list = Field(default_factory=list)


class ApproveIn(BaseModel):
    approver_id: str
    reason: str


class FailureInjection(BaseModel):
    action_type: str
    mode: str


class ExecuteIn(BaseModel):
    idempotency_key: str
    failure_injection: Optional[FailureInjection] = None


class PlanIn(BaseModel):
    scenario: str
    actions: List[ActionIn]
    requires_approval: bool = True
    plan_id: Optional[str] = None


def _plan_payload(body: PlanIn):
    return body.model_dump()


def _call(func, *args):
    try:
        return func(*args)
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlanExists as exc:
        raise HTTPException(status_code=409, detail="plan already exists: %s" % exc) from exc
    except PlanNotFound as exc:
        raise HTTPException(status_code=404, detail="plan not found: %s" % exc) from exc
    except ExecutionRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GraphUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def create_app():
    app = FastAPI(title="ActionGraph", version="0.3.0")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return HTMLResponse(_INDEX.read_text(encoding="utf-8"))

    @app.get("/v1/demo/fixtures")
    def get_fixtures():
        return demo.fixtures()

    @app.post("/v1/demo/reset")
    def post_reset():
        return _call(demo.reset)

    @app.post("/v1/demo/ablation")
    def post_ablation():
        return _call(demo.ablation)

    @app.get("/v1/metrics")
    def get_metrics():
        return snapshot()

    @app.get("/health")
    def health():
        try:
            ping()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="FalkorDB is unavailable: %s" % exc) from exc
        return {"falkordb": "ok"}

    @app.post("/v1/plans", status_code=201)
    def post_plan(body: PlanIn):
        return _call(create_plan, _plan_payload(body))

    @app.post("/v1/plans/{plan_id}/preflight")
    def post_preflight(plan_id: str):
        return _call(preflight_plan, plan_id)

    @app.post("/v1/plans/{plan_id}/approve")
    def post_approve(plan_id: str, body: ApproveIn):
        return _call(approve_plan, plan_id, body.approver_id, body.reason)

    @app.post("/v1/plans/{plan_id}/execute")
    def post_execute(plan_id: str, body: ExecuteIn):
        injection = body.failure_injection.model_dump() if body.failure_injection else None
        return _call(execute_plan, plan_id, body.idempotency_key, None, injection)

    @app.post("/v1/executions/{execution_id}/reconcile")
    def post_reconcile(execution_id: str):
        return _call(reconcile_execution, execution_id)

    @app.get("/v1/plans/{plan_id}/impact")
    def get_impact(plan_id: str):
        return _call(plan_impact, plan_id)

    @app.get("/v1/plans/{plan_id}/report")
    def get_report(plan_id: str):
        return _call(build_report, plan_id)

    @app.get("/v1/plans/{plan_id}")
    def get_one_plan(plan_id: str):
        plan = _call(get_plan, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="plan not found: %s" % plan_id)
        return plan

    return app


app = create_app()
