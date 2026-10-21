"""Admin login and worker upload links.

One admin (password from .env) uses the dashboard. Workers never log in: each project
has a signed upload link that only allows submitting proof for that project.

Tokens are HMAC-signed with SECRET_KEY, so no session table is needed.
"""
import hashlib
import hmac
import secrets
import time
import warnings

from fastapi import Header, HTTPException

from .config import settings

ADMIN_TOKEN_TTL = 12 * 3600

if settings.SECRET_KEY:
    _key = settings.SECRET_KEY.encode()
else:
    # Works, but logins and worker links stop working whenever the server restarts.
    warnings.warn("SECRET_KEY not set in .env; using a temporary key.")
    _key = secrets.token_bytes(32)


def _sign(payload: str) -> str:
    return hmac.new(_key, payload.encode(), hashlib.sha256).hexdigest()


def check_password(password: str) -> bool:
    if not settings.ADMIN_PASSWORD:
        raise HTTPException(503, "ADMIN_PASSWORD is not set in backend/.env")
    return hmac.compare_digest(password.encode(), settings.ADMIN_PASSWORD.encode())


def make_admin_token() -> str:
    payload = f"admin.{int(time.time()) + ADMIN_TOKEN_TTL}"
    return f"{payload}.{_sign(payload)}"


def is_admin_token(token: str | None) -> bool:
    try:
        role, exp, sig = (token or "").split(".")
    except ValueError:
        return False
    payload = f"{role}.{exp}"
    return (role == "admin" and hmac.compare_digest(sig, _sign(payload))
            and exp.isdigit() and int(exp) > time.time())


def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_admin(authorization: str | None = Header(None)):
    """FastAPI dependency for every dashboard / money endpoint."""
    if not is_admin_token(bearer(authorization)):
        raise HTTPException(401, "Admin login required")


def worker_token(project_id: int) -> str:
    return _sign(f"worker.{project_id}")[:32]


def is_worker_token(project_id: int, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(token, worker_token(project_id))
