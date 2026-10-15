"""Policy layer: the ONLY code allowed to move money.

The AI agent returns a decision. This module checks hard rules, then calls PayPal,
and writes every allowed or blocked action to the audit log.
"""
from sqlalchemy.orm import Session

from . import paypal
from .config import settings
from .models import AuditLog, Milestone, Project


class PolicyBlocked(Exception):
    pass


def log(db: Session, actor: str, action: str, outcome: str, project_id=None, milestone_id=None, **detail):
    db.add(AuditLog(actor=actor, action=action, outcome=outcome,
                    project_id=project_id, milestone_id=milestone_id, detail=detail))
    db.commit()


def _block(db: Session, m: Milestone, actor: str, reason: str):
    log(db, actor, "RELEASE", "BLOCKED", m.project_id, m.id, reason=reason)
    raise PolicyBlocked(reason)


def release_milestone(db: Session, m: Milestone, actor: str) -> Milestone:
    """Capture (if needed) and pay out one milestone.

    actor: 'ai_agent' for automatic release, 'human' for a reviewer's approval.
    """
    p: Project = m.project

    # ---- hard rules (apply no matter what the AI says) ----
    if m.status in ("CAPTURED", "PAID_OUT"):
        _block(db, m, actor, "Milestone already paid.")
    if p.status not in ("AUTHORIZED", "ESCROWED"):
        _block(db, m, actor, f"Project not funded (status {p.status}).")
    if actor == "ai_agent" and m.status != "APPROVED":
        _block(db, m, actor, f"AI cannot release a milestone in status {m.status}.")
    if actor == "ai_agent" and m.amount > settings.MAX_AUTO_PAYOUT:
        m.status = "HUMAN_REVIEW"
        db.commit()
        _block(db, m, actor, f"Amount {m.amount} above auto-payout cap {settings.MAX_AUTO_PAYOUT}; sent to human.")
    previous = [x for x in p.milestones if x.order < m.order]
    if any(x.status != "PAID_OUT" for x in previous):
        _block(db, m, actor, "Earlier milestones are not paid yet.")

    # ---- move money ----
    try:
        if p.escrow_mode == "AUTH_CAPTURE":
            is_last = all(x.status == "PAID_OUT" for x in p.milestones if x.id != m.id)
            cap = paypal.capture_authorization(
                p.paypal_authorization_id, m.amount, final_capture=is_last,
                idempotency_key=f"capture-m{m.id}", note=f"Milestone verified: {m.title}")
            m.paypal_capture_id = cap.get("id")
            m.status = "CAPTURED"
            db.commit()
            log(db, actor, "CAPTURE", "OK", p.id, m.id, capture_id=m.paypal_capture_id, amount=m.amount)

        payout = paypal.send_payout(m.worker_email, m.amount, sender_item_id=f"m{m.id}",
                                    note=f"Payment for verified work: {m.title}")
        m.paypal_payout_batch_id = payout.get("batch_header", {}).get("payout_batch_id")
        m.status = "PAID_OUT"
        db.commit()
        log(db, actor, "PAYOUT", "OK", p.id, m.id, batch_id=m.paypal_payout_batch_id,
            amount=m.amount, receiver=m.worker_email)

        if all(x.status == "PAID_OUT" for x in p.milestones):
            p.status = "COMPLETED"
            db.commit()
    except paypal.PayPalError as e:
        log(db, actor, "RELEASE", "ERROR", p.id, m.id, status_code=e.status_code, body=str(e.body)[:1000])
        raise
    return m


def cancel_remaining(db: Session, p: Project, actor: str, reason: str) -> Project:
    """Return unreleased money to the funder."""
    unpaid = [m for m in p.milestones if m.status != "PAID_OUT"]
    remaining = round(sum(m.amount for m in unpaid), 2)
    try:
        if p.escrow_mode == "AUTH_CAPTURE" and p.paypal_authorization_id:
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
