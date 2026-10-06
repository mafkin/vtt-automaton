from fastapi import FastAPI, BackgroundTasks, status
from pydantic import BaseModel
from app.config import settings
from arq import create_pool
from arq.connections import RedisSettings

app = FastAPI(title="VTT Comic API")
redis_pool = None

@app.on_event("startup")
async def startup():
    global redis_pool
    # redis://redis:6379/0 -> parse to RedisSettings
    # We can just use the default RedisSettings for simplicity if redis is on localhost.
    # But here we parse it:
    redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))

@app.post("/api/v1/comic/{session_id}", status_code=status.HTTP_202_ACCEPTED)
async def trigger_comic_generation(session_id: str):
    await redis_pool.enqueue_job("generate_comic", session_id)
    return {"status": "accepted", "session_id": session_id}
