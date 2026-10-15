import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")
    CURRENCY = os.getenv("CURRENCY", "USD")
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")

    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./payonproof.db")

    AUTO_APPROVE_THRESHOLD = float(os.getenv("AUTO_APPROVE_THRESHOLD", "0.85"))
    HUMAN_REVIEW_THRESHOLD = float(os.getenv("HUMAN_REVIEW_THRESHOLD", "0.5"))
    MAX_AUTO_PAYOUT = float(os.getenv("MAX_AUTO_PAYOUT", "500"))

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")


settings = Settings()
