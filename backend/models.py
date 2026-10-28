from datetime import datetime, timezone
from sqlalchemy import DateTime, String, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

def utcnow():
    return datetime.now(timezone.utc)

class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(2000))
    interval_seconds: Mapped[int] = mapped_column(default=60)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    checks: Mapped[list["Check"]] = relationship(
        back_populates="monitor", cascade="all, delete-orphan"
    )

class Check(Base):
    __tablename__ = "checks"
    __table_args__ = (Index("ix_checks_monitor_checked", "monitor_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id"))
    status: Mapped[str] = mapped_column(str(10))
    status_code: Mapped[int | None]
    response_time_ms: Mapped[int]
    error: Mapped[str | None] = mapped_column(String(200))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    monitor: Mapped["Monitor"] = relationship(back_populates="checks")