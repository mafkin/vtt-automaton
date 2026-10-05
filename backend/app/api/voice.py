import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.auth import require_client
from app.sessions.store import SessionStore, StoredSegment
from app.voice.service import TranscriptSegment, VoiceRulesService

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_client)])


class SegmentResponse(BaseModel):
    ruling_id: str | None
    stored: bool = False


def get_voice_service(request: Request) -> VoiceRulesService:
    return request.app.state.voice_service


def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


@router.post("/transcript/segments", response_model=SegmentResponse)
async def post_segment(
    segment: TranscriptSegment,
    service: Annotated[VoiceRulesService, Depends(get_voice_service)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> SegmentResponse:
    """Feed one transcript segment (from the STT worker, or sent by hand for testing).

    With a ``session_id`` the segment is stored in that session's transcript. If it asks the
    arbiter a question, the ruling is posted to Foundry asynchronously.
    """
    stored = False
    if segment.session_id:
        stored = store.add_segment(
            segment.session_id,
            StoredSegment(
                speaker=segment.speaker,
                speaker_id=segment.speaker_id,
                character=segment.character,
                t_start=segment.t_start,
                t_end=segment.t if segment.t is not None else time.time(),
                text=segment.text.strip(),
            ),
        )
        if not stored:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown session")
    return SegmentResponse(ruling_id=service.handle_segment(segment), stored=stored)
