from app import worker
from app.sessions import ended_sessions_with_transcripts, transcript


def test_worker_runs_one_comic_step_at_a_time():
    s = worker.WorkerSettings
    assert {f.__name__ for f in s.functions} == {"extract_job", "script_job", "draw_job"}
    assert s.max_jobs == 1 and s.max_tries == 1 and s.keep_result == 0
    # No GPU handover any more: nothing to recover at startup.
    assert getattr(s, "on_startup", None) is None


async def test_jobs_call_the_pipeline_steps(monkeypatch):
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(worker.pipeline, "extract", fake)
    monkeypatch.setattr(worker.pipeline, "script", fake)
    monkeypatch.setattr(worker.pipeline, "draw", fake)
    await worker.extract_job({}, "c1")
    await worker.script_job({}, "c1", [0, 2], "oma")
    await worker.draw_job({}, "c1", 1, "kilpi")
    assert calls == [
        (("c1",), {}),
        (("c1", [0, 2], "oma"), {}),
        (("c1", 1, "kilpi"), {}),
    ]


def test_transcript_is_in_time_order_with_characters(sessions_db):
    assert transcript("ended1") == "GM: Örkit hyökkäävät.\nAino (Valeros): Hyökkään!"


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


def test_character_tags_with_line_counts(sessions_db):
    from app.sessions import character_tags

    assert character_tags("ended1") == [("Valeros", 1)]
