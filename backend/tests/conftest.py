"""Test setup: a throwaway database and upload folder, and a fake PayPal.

Run from backend/:   python -m pytest
No network calls are made; every money-moving PayPal function is replaced.
"""
import io
import os
import tempfile
from datetime import datetime

import pytest

# Must be set before `app` is imported (config reads env at import; load_dotenv won't override).
_tmp = tempfile.mkdtemp(prefix="pop-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["UPLOAD_DIR"] = os.path.join(_tmp, "uploads")
os.environ["MAX_AUTO_PAYOUT"] = "500"
os.environ["AUTO_APPROVE_THRESHOLD"] = "0.85"
os.environ["HUMAN_REVIEW_THRESHOLD"] = "0.5"
os.environ["ADMIN_PASSWORD"] = "test-password"
os.environ["SECRET_KEY"] = "test-secret"
# Never call a real vision API from tests (load_dotenv won't override these); vision tests use fakes.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["VISION_PROVIDER"] = "auto"
os.environ["GEMINI_MODEL"] = "gemini-3.8-flash"
os.environ["GEMINI_FALLBACK_MODEL"] = "gemini-3.5-flash-lite"
os.environ["GEMINI_THINKING"] = "low"

import numpy as np  # noqa: E402  (installed with imagehash)
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import paypal  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

SITE = (20.5, 86.4)


class FakePayPal:
    """Records calls; set `fail_payout` / `fail_capture` to simulate PayPal errors."""

    def __init__(self):
        self.calls = []
        self.fail_payout = 0
        self.fail_capture = 0

    def create_order(self, **kw):
        self.calls.append(("create_order", kw))
        return {"id": "ORDER1", "links": [{"rel": "payer-action", "href": "https://paypal.test/approve"}]}

    def authorize_order(self, order_id):
        self.calls.append(("authorize_order", order_id))
        return {"purchase_units": [{"payments": {"authorizations": [
            {"id": "AUTH1", "expiration_time": "2099-01-01T00:00:00Z"}]}}]}

    def capture_order(self, order_id):
        self.calls.append(("capture_order", order_id))
        return {"purchase_units": [{"payments": {"captures": [{"id": "ESCROWCAP1"}]}}]}

    def capture_authorization(self, authorization_id, amount, final_capture, idempotency_key, note=""):
        self.calls.append(("capture_authorization", amount, final_capture, idempotency_key))
        if self.fail_capture:
            self.fail_capture -= 1
            raise paypal.PayPalError(500, "capture down")
        return {"id": f"CAP-{idempotency_key}"}

    def send_payout(self, receiver_email, amount, sender_item_id, note):
        self.calls.append(("send_payout", receiver_email, amount, sender_item_id))
        if self.fail_payout:
            self.fail_payout -= 1
            raise paypal.PayPalError(503, "payouts down")
        return {"batch_header": {"payout_batch_id": f"BATCH-{sender_item_id}"}}

    payout_item_status = "SUCCESS"

    def get_payout_batch(self, batch_id):
        self.calls.append(("get_payout_batch", batch_id))
        sender_item_id = batch_id.removeprefix("BATCH-")
        return {"batch_header": {"payout_batch_id": batch_id, "batch_status": "SUCCESS"},
                "items": [{"payout_item_id": f"ITEM-{sender_item_id}", "transaction_status": self.payout_item_status,
                           "payout_item": {"sender_item_id": sender_item_id}}]}

    def void_authorization(self, authorization_id):
        self.calls.append(("void_authorization", authorization_id))
        return {}

    def refund_capture(self, capture_id, amount, idempotency_key, note=""):
        self.calls.append(("refund_capture", capture_id, amount))
        return {}

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def fake_paypal(monkeypatch):
    fake = FakePayPal()
    for name in ("create_order", "authorize_order", "capture_order", "capture_authorization",
                 "send_payout", "get_payout_batch", "void_authorization", "refund_capture"):
        monkeypatch.setattr(paypal, name, getattr(fake, name))
    return fake


@pytest.fixture
def anon():
    """Client with no credentials (a worker, or an attacker)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    os.makedirs(os.environ["UPLOAD_DIR"], exist_ok=True)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(anon):
    """Client logged in as the admin."""
    with TestClient(app) as c:
        token = c.post("/auth/login", json={"password": "test-password"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


@pytest.fixture
def real_vision(monkeypatch):
    """Pretend a vision model is connected and returns `score` (default 0.9)."""
    from app import verifier

    def install(score=0.9):
        monkeypatch.setattr(verifier, "vision_assess", lambda path, desc: {
            "score": score, "hard_flag": False, "connected": True, "reason": "fake model"})
    install()
    return install


_seed = [0]


def make_photo(lat=SITE[0], lon=SITE[1], taken: datetime | None = None, offset: str | None = None,
               camera=True) -> bytes:
    """A JPEG with random content (so hashes never collide) and the given EXIF."""
    _seed[0] += 1
    rng = np.random.default_rng(_seed[0])
    img = Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).resize((256, 256))
    exif = Image.Exif()
    if camera:
        exif[0x010F] = "TestCam"
        exif[0x0110] = "Model 1"
    exif_ifd = exif.get_ifd(0x8769)
    if taken:
        exif_ifd[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")
        if offset:
            exif_ifd[0x9011] = offset
    if lat is not None:
        gps = exif.get_ifd(0x8825)
        gps[1] = "N" if lat >= 0 else "S"
        gps[2] = _dms(abs(lat))
        gps[3] = "E" if lon >= 0 else "W"
        gps[4] = _dms(abs(lon))
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _dms(deg: float):
    d = int(deg)
    m = int((deg - d) * 60)
    s = round(((deg - d) * 60 - m) * 60, 2)
    return (float(d), float(m), s)


def make_funded_project(client, amounts=(30, 40, 30), escrow_mode="AUTH_CAPTURE", title="Mangroves"):
    p = client.post("/projects", json={
        "title": title, "escrow_mode": escrow_mode,
        "site_lat": SITE[0], "site_lon": SITE[1], "site_radius_m": 1000,
        "milestones": [{"title": f"M{i}", "proof_description": "work done", "amount": a,
                        "worker_email": "worker@example.com"} for i, a in enumerate(amounts)],
    }).json()
    client.post(f"/projects/{p['id']}/fund")
    client.get(f"/paypal/return?project_id={p['id']}")
    return client.get(f"/projects/{p['id']}").json()


def upload(client, milestone_id, data: bytes, name="proof.jpg"):
    return client.post(f"/milestones/{milestone_id}/proof", files={"file": (name, data, "image/jpeg")})
