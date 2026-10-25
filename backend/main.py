import os
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "")
engine = create_engine(DATABASE_URL) if DATABASE_URL else None
app = FastAPI(title="Uptime Monitor")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/health/db")
def health_check_db():
    if engine is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not set")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception :
        raise HTTPException(status_code=503, detail="Database not reachable")
    return {"status": "ok"}