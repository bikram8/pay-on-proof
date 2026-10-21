"""Pay-on-Proof API.

Run:  uvicorn app.main:app --reload     (from the backend/ folder)
Docs: http://localhost:8000/docs

Access:
  admin  - logs in with ADMIN_PASSWORD (POST /auth/login), sends `Authorization: Bearer <token>`.
           Every project, money and audit endpoint requires this.
  worker - no login; uses the project's signed upload link (?t=<worker token>) to see the
           project's milestones and submit proof. Nothing else.
  public - /health, the PayPal return/cancel pages and the PayPal webhook.
"""
import html
import os
import uuid

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import auth, paypal, policy, verifier
from .auth import require_admin
from .config import settings
from .db import get_db, init_db
from .models import AuditLog, Milestone, Project, Submission

init_db()
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="Pay-on-Proof", description="AI-verified escrow on PayPal")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ADMIN = [Depends(require_admin)]
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
# MPO is the multi-picture JPEG many phone cameras save.
ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp"}


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


class LoginIn(BaseModel):
    password: str


class ReviewIn(BaseModel):
    approve: bool
    reviewer_note: str = ""


def _project_out(p: Project) -> dict:
    """Full project view for the admin dashboard."""
    return {
        "id": p.id, "title": p.title, "description": p.description, "status": p.status,
        "escrow_mode": p.escrow_mode, "total_amount": p.total_amount, "currency": p.currency,
        "site_lat": p.site_lat, "site_lon": p.site_lon, "site_radius_m": p.site_radius_m,
        "paypal_order_id": p.paypal_order_id, "paypal_authorization_id": p.paypal_authorization_id,
        "created_at": p.created_at.isoformat(),
        "paid_out": round(sum(m.amount for m in p.milestones if m.status == "PAID_OUT"), 2),
        "worker_token": auth.worker_token(p.id),
        "milestones": [{
            "id": m.id, "order": m.order, "title": m.title, "amount": m.amount,
            "status": m.status, "worker_email": m.worker_email,
            "proof_description": m.proof_description, "starts_at": m.starts_at.isoformat(),
            "paypal_capture_id": m.paypal_capture_id, "paypal_payout_batch_id": m.paypal_payout_batch_id,
            "payout_status": m.payout_status,
        } for m in p.milestones],
    }


def _submission_out(s: Submission) -> dict:
    return {"id": s.id, "milestone_id": s.milestone_id, "decision": s.decision,
            "confidence": s.confidence, "reasons": s.reasons, "checks": s.check_results,
            "created_at": s.created_at.isoformat()}


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def _get_milestone(db: Session, milestone_id: int) -> Milestone:
    m = db.get(Milestone, milestone_id)
    if not m:
        raise HTTPException(404, "Milestone not found")
    return m


# ---------- auth ----------

@app.get("/health")
def health():
    return {"ok": True}


@app.post("/auth/login")
def login(body: LoginIn):
    if not auth.check_password(body.password):
        raise HTTPException(401, "Wrong password")
    return {"token": auth.make_admin_token(), "expires_in": auth.ADMIN_TOKEN_TTL}


@app.get("/auth/me", dependencies=ADMIN)
def me():
    return {"role": "admin"}


# ---------- projects & funding (admin) ----------

@app.post("/projects", dependencies=ADMIN)
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    if not body.milestones:
        raise HTTPException(400, "A project needs at least one milestone")
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
    policy.log(db, "admin", "CREATE_PROJECT", "OK", p.id, total=total, escrow_mode=p.escrow_mode)
    return _project_out(p)


@app.get("/projects", dependencies=ADMIN)
def list_projects(db: Session = Depends(get_db)):
    return [_project_out(p) for p in db.query(Project).order_by(Project.id.desc()).all()]


@app.get("/projects/{project_id}", dependencies=ADMIN)
def get_project(project_id: int, db: Session = Depends(get_db)):
    return _project_out(_get_project(db, project_id))


@app.post("/projects/{project_id}/fund", dependencies=ADMIN)
def fund_project(project_id: int, db: Session = Depends(get_db)):
    """Create a PayPal order. Returns the link the funder opens to approve payment."""
    p = _get_project(db, project_id)
    if p.status != "FUNDED_PENDING":
        raise HTTPException(400, f"Project already in status {p.status}")
    intent = "AUTHORIZE" if p.escrow_mode == "AUTH_CAPTURE" else "CAPTURE"
    base = settings.PUBLIC_BASE_URL
    try:
        order = paypal.create_order(
            amount=p.total_amount, intent=intent, reference_id=f"project-{p.id}",
            description=f"Pay-on-Proof escrow: {p.title}",
            return_url=f"{base}/paypal/return?project_id={p.id}",
            cancel_url=f"{base}/paypal/cancel?project_id={p.id}",
        )
    except paypal.PayPalError as e:
        raise HTTPException(502, f"PayPal error {e.status_code} creating the order")
    p.paypal_order_id = order["id"]
    db.commit()
    policy.log(db, "admin", "CREATE_ORDER", "OK", p.id, order_id=order["id"], intent=intent)
    return {"order_id": order["id"], "approve_url": paypal.approval_link(order)}


def _paypal_page(message: str, status_code: int = 200) -> HTMLResponse:
    back = html.escape(settings.FRONTEND_URL)
    return HTMLResponse(
        f"<!doctype html><meta name=viewport content='width=device-width'>"
        f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"<h2>{message}</h2><p><a href='{back}'>Back to the dashboard</a></p></body>",
        status_code=status_code)


@app.get("/paypal/return", response_class=HTMLResponse)
def paypal_return(project_id: int, db: Session = Depends(get_db)):
    """PayPal sends the funder here after approval. We then authorize (hold) or capture (escrow)."""
    p = _get_project(db, project_id)
    if p.status != "FUNDED_PENDING":
        return _paypal_page(f"Project already {html.escape(p.status)}")
    if not p.paypal_order_id:
        return _paypal_page("No PayPal order for this project yet.", 400)
    try:
        if p.escrow_mode == "AUTH_CAPTURE":
            result = paypal.authorize_order(p.paypal_order_id)
            auth_ = result["purchase_units"][0]["payments"]["authorizations"][0]
            p.paypal_authorization_id = auth_["id"]
            p.status = "AUTHORIZED"
            policy.log(db, "funder", "AUTHORIZE", "OK", p.id, authorization_id=auth_["id"],
                       expiration_time=auth_.get("expiration_time"))
        else:
            result = paypal.capture_order(p.paypal_order_id)
            cap = result["purchase_units"][0]["payments"]["captures"][0]
            p.paypal_capture_id = cap["id"]
            p.status = "ESCROWED"
            policy.log(db, "funder", "CAPTURE_TO_ESCROW", "OK", p.id, capture_id=cap["id"])
    except paypal.PayPalError as e:
        policy.log(db, "funder", "FUND", "ERROR", p.id, status_code=e.status_code, body=str(e.body)[:1000])
        return _paypal_page("PayPal could not complete the payment. "
                            "Make sure you approved it, then try the link again.", 502)
    db.commit()
    return _paypal_page(f"Thank you. {p.total_amount:.2f} {html.escape(p.currency)} is now held in escrow "
                        f"for '{html.escape(p.title)}'.")


@app.get("/paypal/cancel", response_class=HTMLResponse)
def paypal_cancel(project_id: int):
    return _paypal_page("Payment cancelled. No money was taken.")


@app.post("/projects/{project_id}/sync-payouts", dependencies=ADMIN)
def sync_payouts(project_id: int, db: Session = Depends(get_db)):
    """Ask PayPal whether each sent payout reached the worker. Complements webhooks,
    which can't reach a server on localhost."""
    p = _get_project(db, project_id)
    errors = 0
    for m in p.milestones:
        if m.status == "PAID_OUT":
            try:
                policy.sync_payout(db, m)
            except paypal.PayPalError:
                errors += 1
    return {**_project_out(p), "sync_errors": errors}


@app.post("/projects/{project_id}/cancel", dependencies=ADMIN)
def cancel_project(project_id: int, reason: str = "Cancelled by admin", db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    try:
        policy.cancel_remaining(db, p, actor="admin", reason=reason)
    except policy.PolicyBlocked as e:
        raise HTTPException(400, str(e))
    except paypal.PayPalError as e:
        raise HTTPException(502, f"PayPal error {e.status_code}; nothing was cancelled")
    return _project_out(p)


# ---------- worker upload page ----------

@app.get("/worker/projects/{project_id}")
def worker_view(project_id: int, t: str = "", db: Session = Depends(get_db)):
    """What the worker's upload page shows. No emails, PayPal ids or scores."""
    if not auth.is_worker_token(project_id, t):
        raise HTTPException(403, "Invalid upload link")
    p = _get_project(db, project_id)
    current = next((m for m in p.milestones if m.status != "PAID_OUT"), None)
    return {
        "title": p.title, "description": p.description, "status": p.status, "currency": p.currency,
        "accepting_proof": p.status in policy.FUNDED_STATUSES,
        "current_milestone_id": current.id if current else None,
        "milestones": [{"id": m.id, "order": m.order, "title": m.title, "amount": m.amount,
                        "proof_description": m.proof_description, "status": m.status}
                       for m in p.milestones],
    }


# ---------- proof submission & verification ----------

def _save_image(file: UploadFile) -> str:
    """Store an upload only if it really is an image within the size limit. Returns its path."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Image larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    tmp = os.path.join(settings.UPLOAD_DIR, f"{uuid.uuid4().hex}.upload")
    with open(tmp, "wb") as out:
        out.write(data)
    try:
        with Image.open(tmp) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        os.remove(tmp)
        raise HTTPException(400, "File is not a readable image")
    if fmt not in ALLOWED_IMAGE_FORMATS:
        os.remove(tmp)
        raise HTTPException(400, f"Unsupported image format {fmt}; use JPEG, PNG or WEBP")
    path = tmp[: -len(".upload")] + ALLOWED_IMAGE_FORMATS[fmt]
    os.replace(tmp, path)
    return path


WORKER_MESSAGES = {
    "PAID_OUT": "Verified and paid. Thank you!",
    "CAPTURED": "Verified. Your payment is being sent.",
    "APPROVED": "Verified. Your payment is being sent.",
    "HUMAN_REVIEW": "Received. A reviewer will check your photo shortly.",
    "REJECTED": "This photo could not be accepted. Please take a new photo at the site and try again.",
}


@app.post("/milestones/{milestone_id}/proof")
def submit_proof(milestone_id: int, file: UploadFile = File(...),
                 t: str | None = Form(None),
                 device_lat: float | None = Form(None), device_lon: float | None = Form(None),
                 device_accuracy_m: float | None = Form(None),
                 authorization: str | None = Header(None),
                 db: Session = Depends(get_db)):
    """Upload proof. Allowed for the admin, or a worker with the project's upload link (`t`).

    device_*: optional phone location from the browser, used if the photo has no GPS.
    Workers get a plain status message; scores and reasons go to the admin only, so they
    can't be used to tune a forged photo.
    """
    m = _get_milestone(db, milestone_id)
    p = m.project
    is_admin = auth.is_admin_token(auth.bearer(authorization))
    if not is_admin and not auth.is_worker_token(p.id, t):
        raise HTTPException(403, "Invalid upload link")
    if m.status not in ("PENDING", "REJECTED"):
        raise HTTPException(400, f"Milestone is {m.status}; cannot accept new proof")
    if p.status not in policy.FUNDED_STATUSES:
        raise HTTPException(400, f"Project is {p.status}; proof is accepted only once it is funded")
    if policy.earlier_unpaid(m):
        raise HTTPException(400, "Finish earlier milestones first; they are not paid out yet")

    device = None
    if device_lat is not None and device_lon is not None:
        device = {"lat": device_lat, "lon": device_lon, "accuracy_m": device_accuracy_m}

    path = _save_image(file)
    previous_hashes = [s.phash for s in db.query(Submission.phash).all()]
    result = verifier.verify(path, m.proof_description, m.starts_at,
                             p.site_lat, p.site_lon, p.site_radius_m, previous_hashes,
                             device_location=device)

    sub = Submission(milestone_id=m.id, image_path=path, phash=result["phash"],
                     confidence=result["confidence"], decision=result["decision"],
                     reasons=result["reasons"], check_results=result["checks"])
    db.add(sub)
    m.status = {"APPROVE": "APPROVED", "REVIEW": "HUMAN_REVIEW", "REJECT": "REJECTED"}[result["decision"]]
    db.commit()
    policy.log(db, "ai_agent", "VERIFY", result["decision"], p.id, m.id,
               confidence=result["confidence"], hard_flags=result["hard_flags"],
               submitted_by="admin" if is_admin else "worker")

    paid = False
    payment_error = None
    if result["decision"] == "APPROVE":
        try:
            policy.release_milestone(db, m, actor="ai_agent")
            paid = True
        except policy.PolicyBlocked as e:
            payment_error = f"Blocked by policy: {e}"
        except paypal.PayPalError as e:
            payment_error = (f"PayPal error {e.status_code}; milestone is {m.status}, "
                             f"retry with POST /milestones/{m.id}/release")

    if not is_admin:
        return {"milestone_status": m.status, "paid": paid, "message": WORKER_MESSAGES.get(m.status, "")}
    return {"submission_id": sub.id, "milestone_status": m.status, "paid": paid,
            "payment_error": payment_error, **{k: result[k] for k in ("decision", "confidence", "hard_flags", "reasons")}}


# ---------- review & release (admin) ----------

@app.get("/review-queue", dependencies=ADMIN)
def review_queue(db: Session = Depends(get_db)):
    """Milestones that need the admin: waiting for review, stuck mid-payment and needing a retry,
    or paid out but not received by the worker (e.g. unclaimed)."""
    rows = (db.query(Milestone)
            .filter(Milestone.status.in_(("HUMAN_REVIEW", "APPROVED", "CAPTURED"))
                    | ((Milestone.status == "PAID_OUT") & Milestone.payout_status.in_(policy.PAYOUT_PROBLEM)))
            .order_by(Milestone.id).all())
    out = []
    for m in rows:
        latest = max(m.submissions, key=lambda s: s.id, default=None)
        needs = ("review" if m.status == "HUMAN_REVIEW" else
                 "payout_problem" if m.status == "PAID_OUT" else "retry")
        out.append({
            "milestone_id": m.id, "milestone_title": m.title, "order": m.order, "amount": m.amount,
            "status": m.status, "worker_email": m.worker_email, "proof_description": m.proof_description,
            "project_id": m.project_id, "project_title": m.project.title, "currency": m.project.currency,
            "payout_status": m.payout_status, "needs": needs,
            "submission": _submission_out(latest) if latest else None,
            "attempts": len(m.submissions),
        })
    return out


@app.post("/milestones/{milestone_id}/review", dependencies=ADMIN)
def human_review(milestone_id: int, body: ReviewIn, db: Session = Depends(get_db)):
    m = _get_milestone(db, milestone_id)
    if m.status != "HUMAN_REVIEW":
        raise HTTPException(400, f"Milestone is {m.status}, not waiting for review")
    policy.log(db, "human", "REVIEW", "APPROVED" if body.approve else "REJECTED",
               m.project_id, m.id, note=body.reviewer_note)
    if not body.approve:
        m.status = "REJECTED"
        db.commit()
        return {"milestone_status": m.status}
    return _release(db, m)


@app.post("/milestones/{milestone_id}/release", dependencies=ADMIN)
def retry_release(milestone_id: int, db: Session = Depends(get_db)):
    """Resume a release that stopped part-way (PayPal error, or a policy block that has since cleared).

    Works for milestones in APPROVED (nothing moved yet) or CAPTURED (captured, payout pending).
    Idempotency keys make it safe to call repeatedly.
    """
    m = _get_milestone(db, milestone_id)
    if m.status not in ("APPROVED", "CAPTURED"):
        raise HTTPException(400, f"Milestone is {m.status}; nothing to retry")
    return _release(db, m)


def _release(db: Session, m: Milestone) -> dict:
    try:
        policy.release_milestone(db, m, actor="human")
    except policy.PolicyBlocked as e:
        raise HTTPException(400, str(e))
    except paypal.PayPalError as e:
        raise HTTPException(502, f"PayPal error {e.status_code}; milestone is {m.status}, "
                                 f"retry with POST /milestones/{m.id}/release")
    return {"milestone_status": m.status}


# ---------- webhooks & dashboard data ----------

@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        genuine = paypal.verify_webhook(headers, event)
    except paypal.PayPalError:
        genuine = False
    event_type = event.get("event_type", "UNKNOWN")
    resource = event.get("resource") or {}
    policy.log(db, "paypal_webhook", event_type[:50], "OK" if genuine else "UNVERIFIED",
               resource_id=resource.get("id") or resource.get("payout_item_id"))
    # Only events PayPal has vouched for may change state.
    if genuine and event_type.startswith("PAYMENT.PAYOUTS-ITEM."):
        _apply_payout_event(db, event_type, resource)
    return {"received": True, "verified": genuine}


def _apply_payout_event(db: Session, event_type: str, resource: dict):
    """PAYMENT.PAYOUTS-ITEM.SUCCEEDED / UNCLAIMED / FAILED / RETURNED / ... -> milestone.payout_status."""
    sender_item_id = (resource.get("payout_item") or {}).get("sender_item_id", "")
    if not (sender_item_id.startswith("m") and sender_item_id[1:].isdigit()):
        return
    m = db.get(Milestone, int(sender_item_id[1:]))
    # The batch id must match what we sent, so an event can't be pointed at another milestone.
    if not m or m.paypal_payout_batch_id != resource.get("payout_batch_id"):
        return
    status = resource.get("transaction_status") or event_type.rsplit(".", 1)[-1]
    status = {"SUCCEEDED": "SUCCESS", "HELD": "ONHOLD"}.get(status, status)
    policy.record_payout_status(db, m, status, resource.get("payout_item_id"), "paypal_webhook")


@app.get("/audit", dependencies=ADMIN)
def audit_log(db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(500).all()
    return [{"id": r.id, "at": r.at.isoformat(), "project_id": r.project_id, "milestone_id": r.milestone_id,
             "actor": r.actor, "action": r.action, "outcome": r.outcome, "detail": r.detail} for r in rows]


@app.get("/submissions", dependencies=ADMIN)
def submissions(db: Session = Depends(get_db)):
    rows = db.query(Submission).order_by(Submission.id.desc()).limit(500).all()
    return [_submission_out(s) for s in rows]


@app.get("/submissions/{submission_id}/image", dependencies=ADMIN)
def submission_image(submission_id: int, db: Session = Depends(get_db)):
    s = db.get(Submission, submission_id)
    if not s or not os.path.isfile(s.image_path):
        raise HTTPException(404, "Image not found")
    return FileResponse(s.image_path)
