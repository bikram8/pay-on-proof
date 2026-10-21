"""End-to-end API flow with a fake PayPal: payment rules, retries and cancellation."""
import os
from datetime import datetime, timedelta

from app.db import SessionLocal
from app.models import Milestone
from tests.conftest import make_funded_project, make_photo, upload


def _photo():
    return make_photo(taken=datetime.utcnow())


def test_stub_vision_sends_good_photo_to_human_then_pays(client, fake_paypal):
    p = make_funded_project(client)
    m = p["milestones"][0]
    r = upload(client, m["id"], _photo()).json()
    assert r["decision"] == "REVIEW" and r["paid"] is False
    assert "send_payout" not in fake_paypal.names()

    r = client.post(f"/milestones/{m['id']}/review", json={"approve": True}).json()
    assert r["milestone_status"] == "PAID_OUT"
    assert fake_paypal.names().count("capture_authorization") == 1


def test_full_auto_flow_and_final_capture(client, fake_paypal, real_vision):
    p = make_funded_project(client)
    for m in p["milestones"]:
        r = upload(client, m["id"], _photo()).json()
        assert r["paid"] is True, r
    captures = [c for c in fake_paypal.calls if c[0] == "capture_authorization"]
    assert [c[2] for c in captures] == [False, False, True]  # only the last is final_capture
    assert client.get(f"/projects/{p['id']}").json()["status"] == "COMPLETED"


def test_out_of_order_proof_is_refused_not_stranded(client, fake_paypal, real_vision):
    p = make_funded_project(client)
    second = p["milestones"][1]
    r = upload(client, second["id"], _photo())
    assert r.status_code == 400
    status = client.get(f"/projects/{p['id']}").json()["milestones"][1]["status"]
    assert status == "PENDING"  # before the fix it got stuck in APPROVED forever


def test_proof_refused_before_funding(client, fake_paypal):
    p = client.post("/projects", json={"title": "x", "milestones": [
        {"title": "a", "proof_description": "b", "amount": 10, "worker_email": "w@example.com"}]}).json()
    assert upload(client, p["milestones"][0]["id"], _photo()).status_code == 400


def test_payout_failure_after_capture_can_be_retried(client, fake_paypal, real_vision):
    p = make_funded_project(client)
    m = p["milestones"][0]
    fake_paypal.fail_payout = 1
    r = upload(client, m["id"], _photo()).json()
    assert r["paid"] is False and r["milestone_status"] == "CAPTURED"
    assert "/release" in r["payment_error"]

    r = client.post(f"/milestones/{m['id']}/release")
    assert r.status_code == 200 and r.json()["milestone_status"] == "PAID_OUT"
    # Money captured exactly once; payout attempted twice with the same item id.
    assert fake_paypal.names().count("capture_authorization") == 1
    assert [c[3] for c in fake_paypal.calls if c[0] == "send_payout"] == ["m1", "m1"]


def test_capture_failure_leaves_approved_and_retry_works(client, fake_paypal, real_vision):
    p = make_funded_project(client)
    m = p["milestones"][0]
    fake_paypal.fail_capture = 1
    r = upload(client, m["id"], _photo()).json()
    assert r["milestone_status"] == "APPROVED"
    assert client.post(f"/milestones/{m['id']}/release").json()["milestone_status"] == "PAID_OUT"


def test_retry_refuses_paid_or_pending_milestones(client, fake_paypal):
    p = make_funded_project(client)
    assert client.post(f"/milestones/{p['milestones'][0]['id']}/release").status_code == 400


def test_payout_restarts_next_milestone_clock(client, fake_paypal, real_vision):
    p = make_funded_project(client)
    # Project started a day ago; the worker took a photo an hour ago, before milestone 0 was paid.
    db = SessionLocal()
    for m in db.query(Milestone).all():
        m.starts_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    db.close()
    old_photo = make_photo(taken=datetime.utcnow() - timedelta(hours=1))
    assert upload(client, p["milestones"][0]["id"], _photo()).json()["paid"] is True
    # Without the clock restart, this hour-old photo would be "after start" and auto-paid.
    r = upload(client, p["milestones"][1]["id"], old_photo).json()
    assert r["decision"] == "REVIEW" and r["paid"] is False
    assert any("before the milestone started" in x for x in r["reasons"])


def test_cancel_blocked_while_payout_pending(client, fake_paypal, real_vision):
    p = make_funded_project(client)
    fake_paypal.fail_payout = 1
    upload(client, p["milestones"][0]["id"], _photo())
    r = client.post(f"/projects/{p['id']}/cancel")
    assert r.status_code == 400 and "not yet paid out" in r.json()["detail"]
    assert "void_authorization" not in fake_paypal.names()


def test_cancel_voids_and_cannot_repeat(client, fake_paypal):
    p = make_funded_project(client)
    assert client.post(f"/projects/{p['id']}/cancel").json()["status"] == "CANCELLED"
    assert client.post(f"/projects/{p['id']}/cancel").status_code == 400
    assert fake_paypal.names().count("void_authorization") == 1


def test_cancel_refunds_remaining_in_escrow_mode(client, fake_paypal, real_vision):
    p = make_funded_project(client, escrow_mode="ESCROW_PAYOUT")
    assert upload(client, p["milestones"][0]["id"], _photo()).json()["paid"] is True
    client.post(f"/projects/{p['id']}/cancel")
    refund = [c for c in fake_paypal.calls if c[0] == "refund_capture"]
    assert refund == [("refund_capture", "ESCROWCAP1", 70.0)]


def test_auto_payout_cap_sends_to_human(client, fake_paypal, real_vision):
    p = make_funded_project(client, amounts=(900,))
    r = upload(client, p["milestones"][0]["id"], _photo()).json()
    assert r["paid"] is False and r["milestone_status"] == "HUMAN_REVIEW"


def test_non_image_upload_rejected_and_not_kept(client, fake_paypal):
    p = make_funded_project(client)
    before = set(os.listdir(os.environ["UPLOAD_DIR"]))
    r = upload(client, p["milestones"][0]["id"], b"<?php echo 'hi'; ?>", name="shell.php")
    assert r.status_code == 400
    assert set(os.listdir(os.environ["UPLOAD_DIR"])) == before


def test_return_page_escapes_title(client, fake_paypal):
    p = make_funded_project(client, title="<script>alert(1)</script>")
    page = client.get(f"/paypal/return?project_id={p['id']}").text  # already funded -> status page
    assert "<script>" not in page
    p2 = client.post("/projects", json={"title": "<img src=x onerror=alert(1)>", "milestones": [
        {"title": "a", "proof_description": "b", "amount": 10, "worker_email": "w@example.com"}]}).json()
    client.post(f"/projects/{p2['id']}/fund")
    page = client.get(f"/paypal/return?project_id={p2['id']}").text
    assert "<img" not in page and "&lt;img" in page
