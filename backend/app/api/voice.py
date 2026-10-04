from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.auth import require_client
from app.voice.service import TranscriptSegment, VoiceRulesService

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_client)])


class SegmentResponse(BaseModel):
    ruling_id: str | None


def get_voice_service(request: Request) -> VoiceRulesService:
    return request.app.state.voice_service


@router.post("/transcript/segments", response_model=SegmentResponse)
async def post_segment(
    segment: TranscriptSegment,
    service: Annotated[VoiceRulesService, Depends(get_voice_service)],
) -> SegmentResponse:
    """Feed one transcript segment (used by the transcription pipeline, and for testing by hand).

    If the segment asks the arbiter a question, the ruling is posted to Foundry asynchronously.
    """
    return SegmentResponse(ruling_id=service.handle_segment(segment))
