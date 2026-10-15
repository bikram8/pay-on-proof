"""Database models.

Escrow modes:
  AUTH_CAPTURE  - funder's money is authorized (held) and captured milestone by milestone.
                  Use for short projects that finish inside PayPal's authorization window.
  ESCROW_PAYOUT - funder's money is captured upfront into the platform account and released
                  to workers via Payouts; refunded to the funder if verification fails.
                  Use for long milestones (e.g. a 60-day survival check).
"""
from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    total_amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    escrow_mode: Mapped[str] = mapped_column(String(20), default="AUTH_CAPTURE")

    # Project site geofence (centre point + radius in metres)
    site_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    site_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    site_radius_m: Mapped[float] = mapped_column(Float, default=500)

    # PayPal references
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paypal_authorization_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ESCROW_PAYOUT only

    # FUNDED_PENDING -> AUTHORIZED / ESCROWED -> COMPLETED / CANCELLED
    status: Mapped[str] = mapped_column(String(20), default="FUNDED_PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    milestones: Mapped[list["Milestone"]] = relationship(back_populates="project", order_by="Milestone.order")


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    proof_description: Mapped[str] = mapped_column(Text)  # what the proof photo must show
    amount: Mapped[float] = mapped_column(Float)
    worker_email: Mapped[str] = mapped_column(String(200))  # sandbox PayPal email of the worker
    starts_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # PENDING -> VERIFYING -> APPROVED/HUMAN_REVIEW/REJECTED -> CAPTURED -> PAID_OUT
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paypal_payout_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    project: Mapped[Project] = relationship(back_populates="milestones")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="milestone")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    milestone_id: Mapped[int] = mapped_column(ForeignKey("milestones.id"))
    image_path: Mapped[str] = mapped_column(String(500))
    phash: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    decision: Mapped[str] = mapped_column(String(20), default="PENDING")  # APPROVE / REVIEW / REJECT
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    check_results: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    milestone: Mapped[Milestone] = relationship(back_populates="submissions")


class AuditLog(Base):
    """Every money-related action and every policy decision. Feeds the AG Grid dashboard."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    milestone_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(30))   # ai_agent / policy / human / paypal_webhook
    action: Mapped[str] = mapped_column(String(50))  # e.g. CAPTURE, PAYOUT, BLOCKED, VOID
    outcome: Mapped[str] = mapped_column(String(20)) # ALLOWED / BLOCKED / OK / ERROR
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
