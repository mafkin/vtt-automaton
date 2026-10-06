from app import worker
from app.config import settings
from app.sessions import transcript


def test_worker_settings_fit_a_long_single_gpu_job():
    s = worker.WorkerSettings
    assert s.job_timeout == settings.job_timeout_seconds >= 3600
    assert s.max_jobs == 1
    assert s.max_tries == 1
    assert s.keep_result == 0


async def test_worker_refuses_live_and_unknown_sessions(sessions_db, monkeypatch):
    ran = []

    async def fake_pipeline(session_id):
        ran.append(session_id)

    monkeypatch.setattr(worker, "run_pipeline", fake_pipeline)
    await worker.generate_comic({}, "live1")
    await worker.generate_comic({}, "nope")
    await worker.generate_comic({}, "ended1")
    assert ran == ["ended1"]


def test_transcript_is_in_time_order_with_characters(sessions_db):
    assert transcript("ended1") == "GM: Örkit hyökkäävät.\nAino (Valeros): Hyökkään!"
