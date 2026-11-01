from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.clients.meta import ChatCompletionsClient
from app.config import get_settings
from app.eligibility import evaluate_eligibility
from app.gateway.proxy import provide_model_client
from app.treasury.agent import run_treasury_agent
from app.treasury.scenarios import get_treasury_scenario, list_treasury_scenarios


router = APIRouter(prefix="/v1/treasury", tags=["treasury-agent"])


class TreasuryScenarioRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, max_length=100)


@router.get("/scenarios")
def scenarios() -> dict[str, Any]:
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="Public demo is disabled")
    return {"scenarios": list_treasury_scenarios()}


@router.post("/run")
def run_scenario(
    body: TreasuryScenarioRequest,
    request: Request,
    model_client: ChatCompletionsClient = Depends(provide_model_client),
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Public demo is disabled")
    selected = get_treasury_scenario(body.scenario_id)
    if selected is None:
        raise HTTPException(status_code=404, detail="Unknown treasury scenario")
    descriptor, task = selected
    result = run_treasury_agent(task, client=model_client)
    payload = result.model_dump(mode="json")
    payload.update(
        {
            "request_id": getattr(request.state, "request_id", None),
            "scenario": descriptor,
            "runtime": evaluate_eligibility(settings).to_dict(),
        }
    )
    return payload
