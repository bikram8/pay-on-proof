"""Policy layer: the ONLY code allowed to move money.

The AI agent returns a decision. This module checks hard rules, then calls PayPal,
and writes every allowed or blocked action to the audit log.

Milestone money states:
  APPROVED  - verified, nothing moved yet
  CAPTURED  - funder's money captured to the platform, worker payout still pending
  PAID_OUT  - worker paid; final
A failed PayPal call leaves the milestone in APPROVED or CAPTURED, and
release_milestone() can be called again to resume from where it stopped.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from . import paypal
from .config import settings
from .models import AuditLog, Milestone, Project

FUNDED_STATUSES = ("AUTHORIZED", "ESCROWED")
# Statuses from which each actor may start or resume a release.
RELEASABLE_BY = {
    "ai_agent": ("APPROVED",),
    "human": ("HUMAN_REVIEW", "APPROVED", "CAPTURED"),
}


class PolicyBlocked(Exception):
    pass


def log(db: Session, actor: str, action: str, outcome: str, project_id=None, milestone_id=None, **detail):
    db.add(AuditLog(actor=actor, action=action, outcome=outcome,
                    project_id=project_id, milestone_id=milestone_id, detail=detail))
    db.commit()


def _block(db: Session, m: Milestone, actor: str, reason: str):
    log(db, actor, "RELEASE", "BLOCKED", m.project_id, m.id, reason=reason)
    raise PolicyBlocked(reason)


def earlier_unpaid(m: Milestone) -> list[Milestone]:
    return [x for x in m.project.milestones if x.order < m.order and x.status != "PAID_OUT"]


def release_milestone(db: Session, m: Milestone, actor: str) -> Milestone:
    """Capture (if needed) and pay out one milestone. Safe to call again after a PayPal failure.

    actor: 'ai_agent' for automatic release, 'human' for a reviewer's approval or a retry.
    """
    p: Project = m.project

    # ---- hard rules (apply no matter what the AI says) ----
    if m.status == "PAID_OUT":
        _block(db, m, actor, "Milestone already paid.")
    if p.status not in FUNDED_STATUSES:
        _block(db, m, actor, f"Project not funded (status {p.status}).")
    if m.status not in RELEASABLE_BY.get(actor, ()):
        _block(db, m, actor, f"{actor} cannot release a milestone in status {m.status}.")
    if actor == "ai_agent" and m.amount > settings.MAX_AUTO_PAYOUT:
        m.status = "HUMAN_REVIEW"
        db.commit()
        _block(db, m, actor, f"Amount {m.amount} above auto-payout cap {settings.MAX_AUTO_PAYOUT}; sent to human.")
    if earlier_unpaid(m):
        _block(db, m, actor, "Earlier milestones are not paid yet.")

    # ---- move money ----
    try:
        if p.escrow_mode == "AUTH_CAPTURE" and m.paypal_capture_id is None:
            others_captured = all(x.paypal_capture_id for x in p.milestones if x.id != m.id)
            cap = paypal.capture_authorization(
                p.paypal_authorization_id, m.amount, final_capture=others_captured,
                idempotency_key=f"capture-m{m.id}", note=f"Milestone verified: {m.title}")
            m.paypal_capture_id = cap.get("id")
            m.status = "CAPTURED"
            db.commit()
            log(db, actor, "CAPTURE", "OK", p.id, m.id, capture_id=m.paypal_capture_id, amount=m.amount)

        payout = paypal.send_payout(m.worker_email, m.amount, sender_item_id=f"m{m.id}",
                                    note=f"Payment for verified work: {m.title}")
        m.paypal_payout_batch_id = payout.get("batch_header", {}).get("payout_batch_id")
        m.status = "PAID_OUT"
        m.payout_status = "PENDING"  # until PayPal confirms the worker received it
        _start_next_milestone(m)
        db.commit()
        log(db, actor, "PAYOUT", "OK", p.id, m.id, batch_id=m.paypal_payout_batch_id,
            amount=m.amount, receiver=m.worker_email)

        if all(x.status == "PAID_OUT" for x in p.milestones):
            p.status = "COMPLETED"
            db.commit()
    except paypal.PayPalError as e:
        db.rollback()
        log(db, actor, "RELEASE", "ERROR", p.id, m.id, status_code=e.status_code,
            resume_from=m.status, body=str(e.body)[:1000])
        raise
    return m


# ---- payout delivery status (PayPal's item status after we sent the payout) ----

PAYOUT_DELIVERED = {"SUCCESS"}
# Worker hasn't got the money and someone has to act (e.g. UNCLAIMED: no PayPal account on that email).
PAYOUT_PROBLEM = {"FAILED", "UNCLAIMED", "RETURNED", "BLOCKED", "DENIED", "CANCELED", "REFUNDED", "REVERSED"}
# No further change expected from PayPal.
PAYOUT_FINAL = {"SUCCESS", "FAILED", "RETURNED", "BLOCKED", "DENIED", "CANCELED", "REFUNDED", "REVERSED"}


def record_payout_status(db: Session, m: Milestone, status: str, item_id: str | None, source: str):
    status = (status or "").upper()[:20]
    if not status or status == m.payout_status:
        return
    previous, m.payout_status = m.payout_status, status
    if item_id:
        m.paypal_payout_item_id = item_id
    db.commit()
    outcome = "OK" if status in PAYOUT_DELIVERED else "ERROR" if status in PAYOUT_PROBLEM else "PENDING"
    log(db, source, "PAYOUT_STATUS", outcome, m.project_id, m.id, status=status, previous=previous,
        receiver=m.worker_email, amount=m.amount)


def sync_payout(db: Session, m: Milestone) -> str | None:
    """Ask PayPal for this milestone's payout item status (works without webhooks)."""
    if not m.paypal_payout_batch_id or m.payout_status in PAYOUT_FINAL:
        return m.payout_status
    batch = paypal.get_payout_batch(m.paypal_payout_batch_id)
    items = batch.get("items", [])
    item = next((i for i in items if i.get("payout_item", {}).get("sender_item_id") == f"m{m.id}"),
                items[0] if items else None)
    if item:
        record_payout_status(db, m, item.get("transaction_status"), item.get("payout_item_id"), "paypal")
    return m.payout_status


def _start_next_milestone(m: Milestone):
    """The next milestone's clock starts now, so its proof photo must be newer than this payout."""
    nxt = next((x for x in m.project.milestones if x.order == m.order + 1), None)
    now = datetime.utcnow()
    if nxt and nxt.starts_at < now:
        nxt.starts_at = now


def cancel_remaining(db: Session, p: Project, actor: str, reason: str) -> Project:
    """Return unreleased money to the funder."""
    if p.status in ("COMPLETED", "CANCELLED"):
        log(db, actor, "CANCEL", "BLOCKED", p.id, reason=f"Project already {p.status}.")
        raise PolicyBlocked(f"Project already {p.status}.")
    pending = [m for m in p.milestones if m.status == "CAPTURED"]
    if pending:
        ids = ", ".join(str(m.id) for m in pending)
        log(db, actor, "CANCEL", "BLOCKED", p.id, reason=f"Payout pending for milestones {ids}.")
        raise PolicyBlocked(f"Verified work on milestone(s) {ids} is captured but not yet paid out. "
                            "Retry the release before cancelling.")

    unpaid = [m for m in p.milestones if m.status != "PAID_OUT"]
    remaining = round(sum(m.amount for m in unpaid), 2)
    try:
        if p.escrow_mode == "AUTH_CAPTURE" and p.paypal_authorization_id and remaining > 0:
            paypal.void_authorization(p.paypal_authorization_id)
            log(db, actor, "VOID", "OK", p.id, reason=reason, amount=remaining)
        elif p.escrow_mode == "ESCROW_PAYOUT" and p.paypal_capture_id and remaining > 0:
            paypal.refund_capture(p.paypal_capture_id, remaining, idempotency_key=f"refund-p{p.id}",
                                  note=reason)
            log(db, actor, "REFUND", "OK", p.id, reason=reason, amount=remaining)
    except paypal.PayPalError as e:
        log(db, actor, "CANCEL", "ERROR", p.id, status_code=e.status_code, body=str(e.body)[:1000])
        raise
    for m in unpaid:
        m.status = "CANCELLED"
    p.status = "CANCELLED"
    db.commit()
    return p
