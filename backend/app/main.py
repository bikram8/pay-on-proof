"""Pay-on-Proof API.

Run:  uvicorn app.main:app --reload     (from the backend/ folder)
Docs: http://localhost:8000/docs
"""
import os
import shutil
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import paypal, policy, verifier
from .config import settings
from .db import Base, engine, get_db
from .models import AuditLog, Milestone, Project, Submission

Base.metadata.create_all(bind=engine)
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="Pay-on-Proof", description="AI-verified escrow on PayPal")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- schemas ----------

class MilestoneIn(BaseModel):
    title: str
    proof_description: str
    amount: float = Field(gt=0)
    worker_email: str


class ProjectIn(BaseModel):
    title: str
    description: str = ""
    escrow_mode: str = Field("AUTH_CAPTURE", pattern="^(AUTH_CAPTURE|ESCROW_PAYOUT)$")
    site_lat: float | None = None
    site_lon: float | None = None
    site_radius_m: float = 500
    milestones: list[MilestoneIn]


def _project_out(p: Project) -> dict:
    return {
        "id": p.id, "title": p.title, "status": p.status, "escrow_mode": p.escrow_mode,
        "total_amount": p.total_amount, "currency": p.currency,
        "paypal_order_id": p.paypal_order_id, "paypal_authorization_id": p.paypal_authorization_id,
        "milestones": [{
            "id": m.id, "order": m.order, "title": m.title, "amount": m.amount,
            "status": m.status, "worker_email": m.worker_email,
            "proof_description": m.proof_description,
        } for m in p.milestones],
    }


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


# ---------- projects & funding ----------

@app.get("/health")
def health():
    return {"ok": True}


@app.post("/projects")
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    total = round(sum(m.amount for m in body.milestones), 2)
    p = Project(title=body.title, description=body.description, total_amount=total,
                currency=settings.CURRENCY, escrow_mode=body.escrow_mode,
                site_lat=body.site_lat, site_lon=body.site_lon, site_radius_m=body.site_radius_m)
    db.add(p)
    db.flush()
    for i, m in enumerate(body.milestones):
        db.add(Milestone(project_id=p.id, order=i, title=m.title, proof_description=m.proof_description,
                         amount=m.amount, worker_email=m.worker_email))
    db.commit()
    db.refresh(p)
    return _project_out(p)


@app.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    return [_project_out(p) for p in db.query(Project).order_by(Project.id.desc()).all()]


@app.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    return _project_out(_get_project(db, project_id))


@app.post("/projects/{project_id}/fund")
def fund_project(project_id: int, db: Session = Depends(get_db)):
    """Create a PayPal order. Returns the link the funder opens to approve payment."""
    p = _get_project(db, project_id)
    if p.status != "FUNDED_PENDING":
        raise HTTPException(400, f"Project already in status {p.status}")
    intent = "AUTHORIZE" if p.escrow_mode == "AUTH_CAPTURE" else "CAPTURE"
    base = settings.PUBLIC_BASE_URL
    order = paypal.create_order(
        amount=p.total_amount, intent=intent, reference_id=f"project-{p.id}",
        description=f"Pay-on-Proof escrow: {p.title}",
        return_url=f"{base}/paypal/return?project_id={p.id}",
        cancel_url=f"{base}/paypal/cancel?project_id={p.id}",
    )
    p.paypal_order_id = order["id"]
    db.commit()
    policy.log(db, "funder", "CREATE_ORDER", "OK", p.id, order_id=order["id"], intent=intent)
    return {"order_id": order["id"], "approve_url": paypal.approval_link(order)}


@app.get("/paypal/return", response_class=HTMLResponse)
def paypal_return(project_id: int, db: Session = Depends(get_db)):
    """PayPal sends the funder here after approval. We then authorize (hold) or capture (escrow)."""
    p = _get_project(db, project_id)
    if p.status != "FUNDED_PENDING":
        return f"<h2>Project already {p.status}</h2>"
    if p.escrow_mode == "AUTH_CAPTURE":
        result = paypal.authorize_order(p.paypal_order_id)
        auth = result["purchase_units"][0]["payments"]["authorizations"][0]
        p.paypal_authorization_id = auth["id"]
        p.status = "AUTHORIZED"
        policy.log(db, "funder", "AUTHORIZE", "OK", p.id, authorization_id=auth["id"],
                   expiration_time=auth.get("expiration_time"))
    else:
        result = paypal.capture_order(p.paypal_order_id)
        cap = result["purchase_units"][0]["payments"]["captures"][0]
        p.paypal_capture_id = cap["id"]
        p.status = "ESCROWED"
        policy.log(db, "funder", "CAPTURE_TO_ESCROW", "OK", p.id, capture_id=cap["id"])
    db.commit()
    return f"<h2>Thank you. {p.total_amount:.2f} {p.currency} is now held in escrow for '{p.title}'.</h2>"


@app.get("/paypal/cancel", response_class=HTMLResponse)
def paypal_cancel(project_id: int):
    return "<h2>Payment cancelled. No money was taken.</h2>"


# ---------- proof submission & verification ----------

@app.post("/milestones/{milestone_id}/proof")
def submit_proof(milestone_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    m = db.get(Milestone, milestone_id)
    if not m:
        raise HTTPException(404, "Milestone not found")
    if m.status not in ("PENDING", "REJECTED"):
        raise HTTPException(400, f"Milestone is {m.status}; cannot accept new proof")

    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    path = os.path.join(settings.UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    previous_hashes = [s.phash for s in db.query(Submission.phash).all()]
    p = m.project
    result = verifier.verify(path, m.proof_description, m.starts_at,
                             p.site_lat, p.site_lon, p.site_radius_m, previous_hashes)

    sub = Submission(milestone_id=m.id, image_path=path, phash=result["phash"],
                     confidence=result["confidence"], decision=result["decision"],
                     reasons=result["reasons"], check_results=result["checks"])
    db.add(sub)
    m.status = {"APPROVE": "APPROVED", "REVIEW": "HUMAN_REVIEW", "REJECT": "REJECTED"}[result["decision"]]
    db.commit()
    policy.log(db, "ai_agent", "VERIFY", result["decision"], p.id, m.id,
               confidence=result["confidence"], hard_flags=result["hard_flags"])

    paid = False
    payment_error = None
    if result["decision"] == "APPROVE":
        try:
            policy.release_milestone(db, m, actor="ai_agent")
            paid = True
        except policy.PolicyBlocked as e:
            payment_error = f"Blocked by policy: {e}"
        except paypal.PayPalError as e:
            payment_error = f"PayPal error {e.status_code}"

    return {"submission_id": sub.id, "milestone_status": m.status, "paid": paid,
            "payment_error": payment_error, **{k: result[k] for k in ("decision", "confidence", "hard_flags", "reasons")}}


class ReviewIn(BaseModel):
    approve: bool
    reviewer_note: str = ""


@app.post("/milestones/{milestone_id}/review")
def human_review(milestone_id: int, body: ReviewIn, db: Session = Depends(get_db)):
    m = db.get(Milestone, milestone_id)
    if not m:
        raise HTTPException(404, "Milestone not found")
    if m.status != "HUMAN_REVIEW":
        raise HTTPException(400, f"Milestone is {m.status}, not waiting for review")
    policy.log(db, "human", "REVIEW", "APPROVED" if body.approve else "REJECTED",
               m.project_id, m.id, note=body.reviewer_note)
    if not body.approve:
        m.status = "REJECTED"
        db.commit()
        return {"milestone_status": m.status}
    try:
        policy.release_milestone(db, m, actor="human")
    except policy.PolicyBlocked as e:
        raise HTTPException(400, str(e))
    return {"milestone_status": m.status}


@app.post("/projects/{project_id}/cancel")
def cancel_project(project_id: int, reason: str = "Cancelled by funder", db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    policy.cancel_remaining(db, p, actor="funder", reason=reason)
    return _project_out(p)


# ---------- webhooks & dashboard data ----------

@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        genuine = paypal.verify_webhook(headers, event)
    except paypal.PayPalError:
        genuine = False
    policy.log(db, "paypal_webhook", event.get("event_type", "UNKNOWN"),
               "OK" if genuine else "UNVERIFIED", resource_id=event.get("resource", {}).get("id"))
    # TODO (Phase 1, day 6): update milestone/project status from events such as
    # PAYMENT.CAPTURE.COMPLETED, PAYMENT.PAYOUTS-ITEM.SUCCEEDED, PAYMENT.PAYOUTS-ITEM.FAILED
    return {"received": True, "verified": genuine}


@app.get("/audit")
def audit_log(db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(500).all()
    return [{"id": r.id, "at": r.at.isoformat(), "project_id": r.project_id, "milestone_id": r.milestone_id,
             "actor": r.actor, "action": r.action, "outcome": r.outcome, "detail": r.detail} for r in rows]


@app.get("/submissions")
def submissions(db: Session = Depends(get_db)):
    rows = db.query(Submission).order_by(Submission.id.desc()).limit(500).all()
    return [{"id": s.id, "milestone_id": s.milestone_id, "decision": s.decision, "confidence": s.confidence,
             "reasons": s.reasons, "created_at": s.created_at.isoformat()} for s in rows]
