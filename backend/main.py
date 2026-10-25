from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from db import engine, get_db
from models import Base, Monitor

@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine is not None:
        Base.metadata.create_all(engine)
    yield

app = FastAPI(title="Uptime Monitor", lifespan=lifespan)

class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2000)
    interval_seconds: int = Field(default=60, ge=30, le=3600)

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str):
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return value

class MonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    interval_seconds: int
    created_at: datetime


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


@app.post("/monitors", response_model=MonitorOut, status_code=201)
def create_monitor(data: MonitorCreate, db: Session = Depends(get_db)):
    monitor = Monitor(
        name=data.name, url=data.url, interval_seconds=data.interval_seconds
    )
    db.add(monitor)
    db.commit()
    db.refresh(monitor)
    return monitor

@app.get("/monitors", response_model=list[MonitorOut])
def list_monitors(db: Session = Depends(get_db)):
    return db.query(Monitor).order_by(Monitor.id).all()


@app.get("/monitors/{monitor_id}", response_model=MonitorOut)
def get_monitor(monitor_id: int, db: Session = Depends(get_db)):
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return monitor


@app.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)):
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    db.delete(monitor)
    db.commit()