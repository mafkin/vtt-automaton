from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.auth import require_client
from app.render.discord import render_discord
from app.render.foundry import render_foundry
from app.rules.models import Ruling, RulingRequest
from app.rules.service import NoRulesFound, RulesService

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_client)])


class RulingResponse(BaseModel):
    ruling: Ruling
    html: str | None = None
    embed: dict | None = None


def get_rules_service(request: Request) -> RulesService:
    return request.app.state.rules_service


@router.post("/rulings", response_model=RulingResponse)
async def create_ruling(
    body: RulingRequest, service: Annotated[RulesService, Depends(get_rules_service)]
) -> RulingResponse:
    try:
        ruling = await service.rule(body)
    except NoRulesFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "rules.no_match", "message": "No matching rules entries found"},
        ) from None

    response = RulingResponse(ruling=ruling)
    if body.render == "foundry":
        response.html = render_foundry(ruling)
    elif body.render == "discord":
        response.embed = render_discord(ruling)
    return response
