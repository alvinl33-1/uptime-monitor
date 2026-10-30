from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field, field_validator

from db import engine, get_db
from models import Base, Incident, Monitor, Check
from checker import check_url

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


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    monitor_id: int
    status: str
    status_code: int | None
    response_time_ms: int
    error: str | None
    checked_at: datetime


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    monitor_id: int
    started_at: datetime
    resolved_at: datetime | None


def query_incidents(
    db: Session,
    monitor_id: int | None = None,
    open_only: bool = False,
    limit: int = 20,
) -> list[Incident]:
    query = db.query(Incident)
    if monitor_id is not None:
        query = query.filter(Incident.monitor_id == monitor_id)
    if open_only:
        query = query.filter(Incident.resolved_at.is_(None))
    return (
        query
        .order_by(Incident.started_at.desc(), Incident.id.desc())
        .limit(limit)
        .all()
    )


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



@app.post("/monitors/{monitor_id}/check", response_model=CheckOut, status_code=201)
def run_check(monitor_id: int, db: Session = Depends(get_db)):
    monitor = db.get(Monitor, monitor_id)

    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    result = check_url(monitor.url)

    check = Check(monitor_id=monitor.id, **result)
    db.add(check)
    db.commit()
    db.refresh(check)
    return check


@app.get("/monitors/{monitor_id}/checks", response_model=list[CheckOut])
def list_checks(monitor_id: int, limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    if (db.get(Monitor, monitor_id) is None):
        raise HTTPException(status_code=404, detail="Monitor not found")
    return (
        db.query(Check)
        .filter(Check.monitor_id == monitor_id)
        .order_by(Check.checked_at.desc(), Check.id.desc())
        .limit(limit)
        .all()
    )




@app.get("/incidents", response_model=list[IncidentOut])
def list_incidents(
    open_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return query_incidents(db, open_only=open_only, limit=limit)


@app.get("/monitors/{monitor_id}/incidents", response_model=list[IncidentOut])
def list_monitor_incidents(
    monitor_id: int,
    open_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if db.get(Monitor, monitor_id) is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return query_incidents(
        db,
        monitor_id=monitor_id,
        open_only=open_only,
        limit=limit,
    )