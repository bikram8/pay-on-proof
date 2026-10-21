"""The verification agent.

It inspects proof with independent checks ("tools") and returns a DECISION.
It never calls PayPal. Money only moves in app/policy.py.

Each check returns:
    {"score": 0..1, "hard_flag": bool, "reason": str, "blocks_auto": bool (optional)}
A hard_flag forces REJECT regardless of the total score.
blocks_auto caps the decision at REVIEW: a human must look, but it is not an outright reject.

Metadata, geofence, duplicate and timing checks run locally; the vision check asks Claude.
"""
import math
from datetime import datetime, timedelta, timezone

import exifread
import imagehash
from PIL import Image

from . import vision
from .config import settings

# Weights must sum to 1.0. Tune these during testing and update the README.
WEIGHTS = {
    "metadata": 0.15,
    "geofence": 0.25,
    "duplicate": 0.20,
    "timing": 0.10,
    "vision": 0.30,
}

DUPLICATE_HAMMING_THRESHOLD = 6  # phash distance <= this counts as the same photo

# Metadata, GPS and timestamps all come from the uploaded file and can be forged,
# so they alone must never be enough to pay. Auto-approval also requires a real
# vision assessment scoring at least this much; otherwise the best outcome is REVIEW.
VISION_MIN_FOR_AUTO_APPROVE = 0.6

# Largest real UTC offset is +14h; a photo "taken" later than this is from the future.
MAX_CLOCK_SKEW = timedelta(hours=14)
# EXIF has 1-second precision and camera clocks drift; allow this much before the start.
TIMING_GRACE = timedelta(minutes=5)


# ---------- helpers ----------

def compute_phash(image_path: str) -> str:
    with Image.open(image_path) as img:
        return str(imagehash.phash(img))


def _read_exif(image_path: str) -> dict:
    with open(image_path, "rb") as f:
        return exifread.process_file(f, details=False)


def _dms_to_deg(values, ref) -> float:
    d, m, s = [float(v.num) / float(v.den) for v in values]
    deg = d + m / 60 + s / 3600
    return -deg if str(ref) in ("S", "W") else deg


def _gps_from_exif(tags: dict) -> tuple[float, float] | None:
    try:
        lat = _dms_to_deg(tags["GPS GPSLatitude"].values, tags["GPS GPSLatitudeRef"])
        lon = _dms_to_deg(tags["GPS GPSLongitude"].values, tags["GPS GPSLongitudeRef"])
        return lat, lon
    except (KeyError, ZeroDivisionError, ValueError):
        return None


def _taken_at(tags: dict) -> datetime | None:
    """Capture time as naive UTC (to compare with DB timestamps).

    EXIF times are camera-local. If the photo records its UTC offset we convert;
    otherwise the local time is used as-is.
    """
    raw = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    if not raw:
        return None
    try:
        taken = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    offset = tags.get("EXIF OffsetTimeOriginal") or tags.get("EXIF OffsetTime")
    if offset:
        try:
            tz = datetime.strptime(str(offset).strip(), "%z").tzinfo
            taken = taken.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    return taken


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------- checks (the agent's tools) ----------

def check_metadata(tags: dict) -> dict:
    has_camera = "Image Make" in tags or "Image Model" in tags
    has_time = _taken_at(tags) is not None
    has_gps = _gps_from_exif(tags) is not None
    present = sum([has_camera, has_time, has_gps])
    if present == 0:
        return {"score": 0.0, "hard_flag": False,
                "reason": "No camera metadata at all: typical of downloaded or screenshotted images."}
    return {"score": present / 3, "hard_flag": False,
            "reason": f"Metadata present: camera={has_camera}, time={has_time}, gps={has_gps}."}


def check_geofence(tags: dict, site_lat, site_lon, radius_m: float, device: dict | None = None) -> dict:
    """device: {"lat", "lon", "accuracy_m"} from the browser at upload time, used only when
    the photo itself has no GPS (mobile browsers often strip it). It proves where the worker
    was when uploading, not where the photo was taken, so it scores lower than photo GPS."""
    if site_lat is None or site_lon is None:
        return {"score": 0.5, "hard_flag": False, "reason": "Project has no site location set."}
    gps = _gps_from_exif(tags)
    if gps is None and device:
        return _check_device_location(device, site_lat, site_lon, radius_m)
    if gps is None:
        return {"score": 0.2, "hard_flag": False, "reason": "Photo has no GPS location."}
    dist = _haversine_m(gps[0], gps[1], site_lat, site_lon)
    if dist <= radius_m:
        return {"score": 1.0, "hard_flag": False, "reason": f"Taken {dist:.0f} m from the site (inside boundary)."}
    hard = dist > radius_m * 10
    return {"score": 0.0, "hard_flag": hard,
            "reason": f"Taken {dist / 1000:.1f} km from the site (outside {radius_m:.0f} m boundary)."}


DEVICE_LOCATION_SCORE = 0.7  # inside the boundary, but weaker evidence than photo GPS


def _check_device_location(device: dict, site_lat, site_lon, radius_m: float) -> dict:
    dist = _haversine_m(device["lat"], device["lon"], site_lat, site_lon)
    accuracy = device.get("accuracy_m") or 0
    if dist <= radius_m:
        if accuracy > radius_m:
            return {"score": 0.4, "hard_flag": False,
                    "reason": f"Photo has no GPS; phone location at upload is {dist:.0f} m from the site "
                              f"but only accurate to {accuracy:.0f} m."}
        return {"score": DEVICE_LOCATION_SCORE, "hard_flag": False,
                "reason": f"Photo has no GPS; phone location at upload is {dist:.0f} m from the site "
                          f"(inside boundary, ±{accuracy:.0f} m)."}
    hard = dist - accuracy > radius_m * 10
    return {"score": 0.0, "hard_flag": hard,
            "reason": f"Photo has no GPS; phone location at upload is {dist / 1000:.1f} km from the site "
                      f"(outside {radius_m:.0f} m boundary)."}


def check_duplicate(phash: str, previous_hashes: list[str]) -> dict:
    new = imagehash.hex_to_hash(phash)
    for old in previous_hashes:
        distance = new - imagehash.hex_to_hash(old)
        if distance <= DUPLICATE_HAMMING_THRESHOLD:
            return {"score": 0.0, "hard_flag": True,
                    "reason": f"Matches a previously submitted photo (hash distance {distance})."}
    return {"score": 1.0, "hard_flag": False, "reason": "Not seen before."}


def check_timing(tags: dict, milestone_start: datetime, now: datetime | None = None) -> dict:
    taken = _taken_at(tags)
    if taken is None:
        return {"score": 0.3, "hard_flag": False, "reason": "Photo time unknown."}
    now = now or datetime.utcnow()
    # Not hard flags (camera clocks and missing time zones make this fuzzy), but too
    # serious to be outweighed by other checks: a human must look. See verify().
    if taken > now + MAX_CLOCK_SKEW:
        return {"score": 0.0, "hard_flag": False, "blocks_auto": True,
                "reason": f"Photo timestamp {taken:%Y-%m-%d} is in the future: metadata looks forged."}
    if taken < milestone_start - TIMING_GRACE:
        return {"score": 0.0, "hard_flag": False, "blocks_auto": True,
                "reason": f"Photo taken {taken:%Y-%m-%d %H:%M} UTC, before the milestone started."}
    return {"score": 1.0, "hard_flag": False, "reason": f"Photo taken {taken:%Y-%m-%d}, after milestone start."}


def vision_assess(image_path: str, proof_description: str) -> dict:
    """Claude's visual review (see app/vision.py).

    score = matches_claim * (1 - signs_of_editing_or_ai). Text in the photo that tries to
    instruct the AI is a hard flag. If Claude is unavailable the result has connected=False,
    which verify() caps at human review: a failure can never auto-approve a payment.
    """
    return vision.assess(image_path, proof_description)


# ---------- the agent ----------

def verify(image_path: str, proof_description: str, milestone_start: datetime,
           site_lat, site_lon, radius_m: float, previous_hashes: list[str],
           vision_fn=None, device_location: dict | None = None) -> dict:
    """vision_fn defaults to vision_assess; tests pass a fake to simulate a real model.
    device_location: optional browser geolocation captured at upload (see check_geofence)."""
    tags = _read_exif(image_path)
    phash = compute_phash(image_path)
    vision_fn = vision_fn or vision_assess

    results = {
        "metadata": check_metadata(tags),
        "geofence": check_geofence(tags, site_lat, site_lon, radius_m, device_location),
        "duplicate": check_duplicate(phash, previous_hashes),
        "timing": check_timing(tags, milestone_start),
        "vision": vision_fn(image_path, proof_description),
    }

    confidence = round(sum(WEIGHTS[k] * results[k]["score"] for k in WEIGHTS), 3)
    hard_flags = [k for k, r in results.items() if r["hard_flag"]]

    if hard_flags:
        decision = "REJECT"
    elif confidence >= settings.AUTO_APPROVE_THRESHOLD:
        decision = "APPROVE"
    elif confidence >= settings.HUMAN_REVIEW_THRESHOLD:
        decision = "REVIEW"
    else:
        decision = "REJECT"

    reasons = [f"{k}: {r['reason']}" for k, r in results.items()]

    # Gates: conditions that cap the outcome at REVIEW whatever the weighted score says.
    blockers = [k for k, r in results.items() if r.get("blocks_auto")]
    seen = results["vision"]
    if decision == "APPROVE" and blockers:
        decision = "REVIEW"
        reasons.append(f"gate: {', '.join(blockers)} check failed, so a human must confirm.")
    elif decision == "APPROVE" and not seen.get("connected", True):
        decision = "REVIEW"
        reasons.append("gate: vision review unavailable, so a human must confirm.")
    elif decision == "APPROVE" and seen["score"] < VISION_MIN_FOR_AUTO_APPROVE:
        decision = "REVIEW"
        reasons.append(f"gate: vision score {seen['score']:.2f} below "
                       f"{VISION_MIN_FOR_AUTO_APPROVE} required for auto-approval.")
    return {
        "decision": decision,
        "confidence": confidence,
        "hard_flags": hard_flags,
        "reasons": reasons,
        "checks": results,
        "phash": phash,
    }
