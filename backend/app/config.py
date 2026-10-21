import os
from dotenv import load_dotenv

load_dotenv()

# Render sets this to the service's public https URL; the API is mounted under /api there.
_RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")


class Settings:
    PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")
    CURRENCY = os.getenv("CURRENCY", "USD")
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"{_RENDER_URL}/api" if _RENDER_URL else "http://localhost:8000")

    # Visual review of proof photos. Without any key, every photo goes to human review.
    # VISION_PROVIDER: auto (Claude if its key is set, else Gemini) | claude | gemini
    VISION_PROVIDER = os.getenv("VISION_PROVIDER", "auto").lower()
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    VISION_MODEL = os.getenv("VISION_MODEL", "claude-opus-5-5")
    # Gemini has a free tier (aistudio.google.com > Get API key)
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    # Tried when the main model is overloaded or rate-limited (empty = no fallback)
    GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    # minimal | low | medium | high (empty = model default). Lower is faster.
    GEMINI_THINKING = os.getenv("GEMINI_THINKING", "low")

    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./payonproof.db")

    AUTO_APPROVE_THRESHOLD = float(os.getenv("AUTO_APPROVE_THRESHOLD", "0.85"))
    HUMAN_REVIEW_THRESHOLD = float(os.getenv("HUMAN_REVIEW_THRESHOLD", "0.5"))
    MAX_AUTO_PAYOUT = float(os.getenv("MAX_AUTO_PAYOUT", "500"))

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")

    # Dashboard login and signing key for login tokens / worker links
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    # Where the dashboard runs; used for the "back to dashboard" link after PayPal approval
    FRONTEND_URL = os.getenv("FRONTEND_URL", _RENDER_URL or "http://localhost:5173")


settings = Settings()
