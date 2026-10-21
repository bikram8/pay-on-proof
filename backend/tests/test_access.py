"""Who can do what: admin login, worker upload links, and what each side can see."""
from datetime import datetime

import pytest

from tests.conftest import SITE, make_funded_project, make_photo


def test_login(anon):
    assert anon.post("/auth/login", json={"password": "nope"}).status_code == 401
    r = anon.post("/auth/login", json={"password": "test-password"})
    assert r.status_code == 200
    me = anon.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.status_code == 200


@pytest.mark.parametrize("method,path", [
    ("get", "/projects"), ("post", "/projects/1/fund"), ("post", "/milestones/1/review"),
    ("post", "/milestones/1/release"), ("post", "/projects/1/cancel"), ("get", "/audit"),
    ("get", "/submissions"), ("get", "/review-queue"), ("get", "/submissions/1/image"),
])
def test_money_and_dashboard_endpoints_need_admin(anon, method, path):
    r = getattr(anon, method)(path, headers={"Authorization": "Bearer admin.9999999999.forged"})
    assert r.status_code == 401


def test_worker_link_uploads_and_sees_only_a_message(client, anon, fake_paypal):
    p = make_funded_project(client)
    t = p["worker_token"]
    view = anon.get(f"/worker/projects/{p['id']}", params={"t": t}).json()
    assert view["current_milestone_id"] == p["milestones"][0]["id"]
    assert "worker_email" not in str(view) and "paypal" not in str(view)

    r = anon.post(f"/milestones/{view['current_milestone_id']}/proof", data={"t": t},
                  files={"file": ("p.jpg", make_photo(taken=datetime.utcnow()), "image/jpeg")}).json()
    assert r["milestone_status"] == "HUMAN_REVIEW" and r["message"]
    assert "confidence" not in r and "reasons" not in r


def test_worker_link_is_bound_to_its_project(client, anon, fake_paypal):
    a = make_funded_project(client, title="A")
    b = make_funded_project(client, title="B")
    assert anon.get(f"/worker/projects/{b['id']}", params={"t": a["worker_token"]}).status_code == 403
    r = anon.post(f"/milestones/{b['milestones'][0]['id']}/proof", data={"t": a["worker_token"]},
                  files={"file": ("p.jpg", make_photo(), "image/jpeg")})
    assert r.status_code == 403
    r = anon.post(f"/milestones/{b['milestones'][0]['id']}/proof",
                  files={"file": ("p.jpg", make_photo(), "image/jpeg")})
    assert r.status_code == 403


def test_review_queue_shows_submission_and_image(client, fake_paypal):
    p = make_funded_project(client)
    m = p["milestones"][0]
    client.post(f"/milestones/{m['id']}/proof",
                files={"file": ("p.jpg", make_photo(taken=datetime.utcnow()), "image/jpeg")})
    q = client.get("/review-queue").json()
    assert len(q) == 1 and q[0]["needs"] == "review"
    sub = q[0]["submission"]
    assert set(sub["checks"]) == {"metadata", "geofence", "duplicate", "timing", "vision"}
    img = client.get(f"/submissions/{sub['id']}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_phone_location_used_when_photo_has_no_gps(client, fake_paypal):
    p = make_funded_project(client)
    m = p["milestones"][0]
    r = client.post(f"/milestones/{m['id']}/proof",
                    data={"device_lat": SITE[0] + 0.001, "device_lon": SITE[1], "device_accuracy_m": 20},
                    files={"file": ("p.jpg", make_photo(lat=None, taken=datetime.utcnow()), "image/jpeg")}).json()
    geofence = next(x for x in r["reasons"] if x.startswith("geofence"))
    assert "phone location" in geofence and "inside boundary" in geofence


def test_phone_far_away_is_hard_reject(client, fake_paypal):
    p = make_funded_project(client)
    r = client.post(f"/milestones/{p['milestones'][0]['id']}/proof",
                    data={"device_lat": 28.6, "device_lon": 77.2, "device_accuracy_m": 20},
                    files={"file": ("p.jpg", make_photo(lat=None, taken=datetime.utcnow()), "image/jpeg")}).json()
    assert r["decision"] == "REJECT" and "geofence" in r["hard_flags"]
