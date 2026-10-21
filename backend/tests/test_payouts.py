"""Payout delivery tracking: did the worker actually receive the money?"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db as db_module, paypal
from tests.conftest import make_funded_project, make_photo, upload


def _paid_project(client):
    p = make_funded_project(client, amounts=(30, 40))
    m = p["milestones"][0]
    upload(client, m["id"], make_photo(taken=datetime.utcnow()))
    client.post(f"/milestones/{m['id']}/review", json={"approve": True})
    return client.get(f"/projects/{p['id']}").json()


def _event(event_type, m, status=None, batch=None):
    return {"event_type": event_type, "resource": {
        "payout_item_id": f"ITEM-m{m['id']}", "payout_batch_id": batch or m["paypal_payout_batch_id"],
        "transaction_status": status, "payout_item": {"sender_item_id": f"m{m['id']}"}}}


def test_payout_starts_pending(client, fake_paypal):
    m = _paid_project(client)["milestones"][0]
    assert m["status"] == "PAID_OUT" and m["payout_status"] == "PENDING"


def test_sync_marks_received(client, fake_paypal):
    p = _paid_project(client)
    r = client.post(f"/projects/{p['id']}/sync-payouts").json()
    assert r["milestones"][0]["payout_status"] == "SUCCESS"
    # Final statuses aren't polled again.
    client.post(f"/projects/{p['id']}/sync-payouts")
    assert fake_paypal.names().count("get_payout_batch") == 1


def test_unclaimed_payout_shows_in_review_queue(client, fake_paypal):
    p = _paid_project(client)
    fake_paypal.payout_item_status = "UNCLAIMED"
    client.post(f"/projects/{p['id']}/sync-payouts")
    q = client.get("/review-queue").json()
    assert [(i["needs"], i["payout_status"]) for i in q] == [("payout_problem", "UNCLAIMED")]
    actions = [(a["action"], a["outcome"]) for a in client.get("/audit").json()]
    assert ("PAYOUT_STATUS", "ERROR") in actions


def test_verified_webhook_updates_status(client, fake_paypal, monkeypatch):
    monkeypatch.setattr(paypal, "verify_webhook", lambda headers, event: True)
    p = _paid_project(client)
    m = p["milestones"][0]
    client.post("/webhooks/paypal", json=_event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", m))
    assert client.get(f"/projects/{p['id']}").json()["milestones"][0]["payout_status"] == "SUCCESS"


@pytest.mark.parametrize("genuine,batch", [(False, None), (True, "SOMEONE-ELSES-BATCH")])
def test_unverified_or_mismatched_webhook_is_ignored(client, fake_paypal, monkeypatch, genuine, batch):
    monkeypatch.setattr(paypal, "verify_webhook", lambda headers, event: genuine)
    p = _paid_project(client)
    m = p["milestones"][0]
    client.post("/webhooks/paypal", json=_event("PAYMENT.PAYOUTS-ITEM.FAILED", m, "FAILED", batch))
    assert client.get(f"/projects/{p['id']}").json()["milestones"][0]["payout_status"] == "PENDING"


def test_init_db_adds_new_columns_to_an_old_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:  # milestones table as it was before payout tracking
        c.execute(text("CREATE TABLE milestones (id INTEGER PRIMARY KEY, project_id INTEGER, \"order\" INTEGER, "
                       "title VARCHAR(200), proof_description TEXT, amount FLOAT, worker_email VARCHAR(200), "
                       "starts_at DATETIME, status VARCHAR(20), paypal_capture_id VARCHAR(64), "
                       "paypal_payout_batch_id VARCHAR(64))"))
    monkeypatch.setattr(db_module, "engine", engine)
    db_module.init_db()
    cols = {c["name"] for c in inspect(engine).get_columns("milestones")}
    assert {"payout_status", "paypal_payout_item_id"} <= cols
