from app import worker
from app.config import settings
from app.gpu import recover
from app.sessions import any_live, ended_sessions_with_transcripts, transcript


def test_worker_settings_fit_a_long_single_gpu_job():
    s = worker.WorkerSettings
    assert s.job_timeout == settings.job_timeout_seconds >= 3600
    assert s.max_jobs == 1
    assert s.max_tries == 1
    assert s.keep_result == 0
    # A crash mid-job must not leave STT stopped or ComfyUI holding the GPU.
    assert s.on_startup is recover


async def test_worker_refuses_live_and_unknown_sessions(no_live_sessions, monkeypatch):
    ran = []

    async def fake_pipeline(session_id):
        ran.append(session_id)

    monkeypatch.setattr(worker, "run_pipeline", fake_pipeline)
    await worker.generate_comic({}, "nope")
    await worker.generate_comic({}, "ended1")
    assert ran == ["ended1"]


async def test_worker_refuses_while_any_session_is_live(sessions_db, monkeypatch):
    # ended1 is finished, but live1 is recording: stopping STT now would cut tonight's game.
    ran = []

    async def fake_pipeline(session_id):
        ran.append(session_id)

    monkeypatch.setattr(worker, "run_pipeline", fake_pipeline)
    await worker.generate_comic({}, "ended1")
    assert ran == []


def test_transcript_is_in_time_order_with_characters(sessions_db):
    assert transcript("ended1") == "GM: Örkit hyökkäävät.\nAino (Valeros): Hyökkään!"


def test_any_live(sessions_db, no_live_sessions):
    assert any_live() is False


def test_any_live_with_a_live_session(sessions_db):
    assert any_live() is True


def test_ended_sessions_with_transcripts_skips_live_and_empty(sessions_db):
    import sqlite3

    with sqlite3.connect(sessions_db) as conn:
        conn.execute(
            "INSERT INTO sessions (id, label, source, started_at, ended_at)"
            " VALUES ('empty1', 'No audio', 'discord', 1, 2)"
        )
    conn.close()
    picks = ended_sessions_with_transcripts()
    assert [(p.id, p.segments) for p in picks] == [("ended1", 2)]
    assert picks[0].label == "Session <b>12</b>"
