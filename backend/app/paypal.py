"""Thin PayPal REST client (sandbox).

Only app/policy.py should call the money-moving functions in this module.
The AI agent never imports this file.

Docs:
  Orders v2:        https://developer.paypal.com/docs/api/orders/v2/
  Payments v2:      https://developer.paypal.com/docs/api/payments/v2/
  Payouts v1:       https://developer.paypal.com/docs/api/payments.payouts-batch/v1/
  Webhooks verify:  https://developer.paypal.com/docs/api/webhooks/v1/
"""
import time
import uuid

import httpx

from .config import settings

_token_cache = {"token": None, "expires_at": 0.0}


class PayPalError(Exception):
    def __init__(self, status_code: int, body: dict | str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"PayPal API error {status_code}: {body}")


def get_access_token() -> str:
    """OAuth2 client-credentials token, cached until shortly before expiry."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
        raise RuntimeError("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET missing. Copy .env.example to .env.")

    resp = httpx.post(
        f"{settings.PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise PayPalError(resp.status_code, _safe_json(resp))
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3000)
    return data["access_token"]


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _request(method: str, path: str, json: dict | None = None, idempotency_key: str | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
        # Idempotency: retrying the same call never moves money twice.
        "PayPal-Request-Id": idempotency_key or str(uuid.uuid4()),
    }
    resp = httpx.request(method, f"{settings.PAYPAL_BASE_URL}{path}", json=json, headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise PayPalError(resp.status_code, _safe_json(resp))
    return resp.json() if resp.content else {}


def _money(amount: float, currency: str | None = None) -> dict:
    return {"currency_code": currency or settings.CURRENCY, "value": f"{amount:.2f}"}


# ---------- Orders: funder pays into escrow ----------

def create_order(amount: float, intent: str, reference_id: str, description: str,
                 return_url: str, cancel_url: str) -> dict:
    """intent = 'AUTHORIZE' (hold money) or 'CAPTURE' (take money now into platform escrow)."""
    body = {
        "intent": intent,
        "purchase_units": [{
            "reference_id": reference_id,
            "description": description[:127],
            "amount": _money(amount),
        }],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": "Pay-on-Proof",
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }
    return _request("POST", "/v2/checkout/orders", json=body)


def approval_link(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link["href"]
    return None


def get_order(order_id: str) -> dict:
    return _request("GET", f"/v2/checkout/orders/{order_id}")


def authorize_order(order_id: str) -> dict:
    """After the funder approves an AUTHORIZE order. Returns the order with the authorization id.

    Fixed idempotency key: if the return page is loaded twice, PayPal replays the first result.
    """
    return _request("POST", f"/v2/checkout/orders/{order_id}/authorize", json={},
                    idempotency_key=f"authorize-{order_id}")


def capture_order(order_id: str) -> dict:
    """After the funder approves a CAPTURE order (ESCROW_PAYOUT mode)."""
    return _request("POST", f"/v2/checkout/orders/{order_id}/capture", json={},
                    idempotency_key=f"capture-{order_id}")


# ---------- Authorizations: release held money milestone by milestone ----------

def capture_authorization(authorization_id: str, amount: float, final_capture: bool,
                          idempotency_key: str, note: str = "") -> dict:
    body = {
        "amount": _money(amount),
        "final_capture": final_capture,
        "note_to_payer": note[:255] if note else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return _request("POST", f"/v2/payments/authorizations/{authorization_id}/capture",
                    json=body, idempotency_key=idempotency_key)


def reauthorize(authorization_id: str, amount: float) -> dict:
    """Extend a hold that is about to expire. Check PayPal docs for the allowed window."""
    return _request("POST", f"/v2/payments/authorizations/{authorization_id}/reauthorize",
                    json={"amount": _money(amount)})


def void_authorization(authorization_id: str) -> dict:
    """Release the remaining hold back to the funder."""
    return _request("POST", f"/v2/payments/authorizations/{authorization_id}/void")


def get_authorization(authorization_id: str) -> dict:
    return _request("GET", f"/v2/payments/authorizations/{authorization_id}")


def refund_capture(capture_id: str, amount: float | None, idempotency_key: str, note: str = "") -> dict:
    body: dict = {}
    if amount is not None:
        body["amount"] = _money(amount)
    if note:
        body["note_to_payer"] = note[:255]
    return _request("POST", f"/v2/payments/captures/{capture_id}/refund", json=body,
                    idempotency_key=idempotency_key)


# ---------- Payouts: pay the field worker ----------

def send_payout(receiver_email: str, amount: float, sender_item_id: str, note: str) -> dict:
    body = {
        "sender_batch_header": {
            "sender_batch_id": f"pop-{sender_item_id}",  # unique per payout = no double pay
            "email_subject": "You've been paid for verified work",
            "email_message": note[:255],
        },
        "items": [{
            "recipient_type": "EMAIL",
            "amount": {"value": f"{amount:.2f}", "currency": settings.CURRENCY},
            "receiver": receiver_email,
            "note": note[:255],
            "sender_item_id": sender_item_id,
        }],
    }
    # Same key on retry: if the first attempt reached PayPal but we never saw the reply,
    # PayPal returns the original batch instead of rejecting the reused sender_batch_id.
    return _request("POST", "/v1/payments/payouts", json=body, idempotency_key=f"payout-{sender_item_id}")


def get_payout_batch(batch_id: str) -> dict:
    return _request("GET", f"/v1/payments/payouts/{batch_id}")


# ---------- Webhooks ----------

def verify_webhook(headers: dict, event: dict) -> bool:
    """Ask PayPal whether a received webhook is genuine."""
    if not settings.PAYPAL_WEBHOOK_ID:
        return False
    body = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": settings.PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }
    result = _request("POST", "/v1/notifications/verify-webhook-signature", json=body)
    return result.get("verification_status") == "SUCCESS"
