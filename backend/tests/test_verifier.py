"""Decision math and the vision gate."""
from datetime import datetime, timedelta

from app import verifier
from tests.conftest import SITE, make_photo


def _verify(tmp_path, data, start=None, vision_fn=None, previous=()):
    path = tmp_path / "p.jpg"
    path.write_bytes(data)
    start = start or datetime.utcnow() - timedelta(hours=1)
    return verifier.verify(str(path), "work done", start, SITE[0], SITE[1], 1000,
                           list(previous), vision_fn=vision_fn)


def _vision(score, connected=True):
    return lambda path, desc: {"score": score, "hard_flag": False, "connected": connected, "reason": "fake"}


def test_forged_perfect_metadata_without_vision_goes_to_human(tmp_path):
    # Before the vision gate, forged metadata alone scored 0.91 and was paid automatically.
    r = _verify(tmp_path, make_photo(taken=datetime.utcnow()))  # no API key in tests
    assert r["confidence"] >= 0.85
    assert r["decision"] == "REVIEW"
    assert any("vision review unavailable" in x for x in r["reasons"])


def test_real_vision_with_good_score_auto_approves(tmp_path):
    r = _verify(tmp_path, make_photo(taken=datetime.utcnow()), vision_fn=_vision(0.9))
    assert r["decision"] == "APPROVE"


def test_weak_vision_blocks_auto_approval_even_above_threshold(tmp_path):
    # 0.70 from metadata checks + 0.30 * 0.55 = 0.865 >= 0.85, but vision is not convincing.
    r = _verify(tmp_path, make_photo(taken=datetime.utcnow()), vision_fn=_vision(0.55))
    assert r["confidence"] >= 0.85
    assert r["decision"] == "REVIEW"


def test_no_metadata_rejects(tmp_path):
    r = _verify(tmp_path, make_photo(lat=None, camera=False))
    assert r["decision"] == "REJECT"


def test_duplicate_is_hard_reject(tmp_path):
    data = make_photo(taken=datetime.utcnow())
    first = _verify(tmp_path, data, vision_fn=_vision(0.9))
    again = _verify(tmp_path, data, vision_fn=_vision(0.9), previous=[first["phash"]])
    assert again["decision"] == "REJECT" and again["hard_flags"] == ["duplicate"]


def test_far_outside_geofence_is_hard_reject(tmp_path):
    r = _verify(tmp_path, make_photo(lat=28.6, lon=77.2, taken=datetime.utcnow()), vision_fn=_vision(0.9))
    assert "geofence" in r["hard_flags"] and r["decision"] == "REJECT"


def test_future_timestamp_scores_zero(tmp_path):
    r = _verify(tmp_path, make_photo(taken=datetime.utcnow() + timedelta(days=3)))
    assert r["checks"]["timing"]["score"] == 0.0
    assert "future" in r["checks"]["timing"]["reason"]


def test_photo_before_milestone_start_cannot_auto_approve(tmp_path):
    # Without the gate this scored 0.87 (timing weighs only 0.10) and was auto-paid.
    start = datetime.utcnow()
    r = _verify(tmp_path, make_photo(taken=start - timedelta(days=2)), start=start, vision_fn=_vision(0.9))
    assert r["checks"]["timing"]["score"] == 0.0
    assert r["confidence"] >= 0.85
    assert r["decision"] == "REVIEW"


def test_future_timestamp_cannot_auto_approve(tmp_path):
    r = _verify(tmp_path, make_photo(taken=datetime.utcnow() + timedelta(days=3)), vision_fn=_vision(0.9))
    assert r["decision"] == "REVIEW"


def test_exif_utc_offset_is_applied(tmp_path):
    path = tmp_path / "p.jpg"
    path.write_bytes(make_photo(taken=datetime(2026, 10, 16, 10, 0, 0), offset="+05:30"))
    tags = verifier._read_exif(str(path))
    assert verifier._taken_at(tags) == datetime(2026, 10, 16, 4, 30, 0)
