"""Phase 0 checkpoint: confirm your sandbox credentials work.

Run from backend/:   python -m scripts.check_paypal
"""
from app import paypal

token = paypal.get_access_token()
print("PayPal sandbox connected. Token starts with:", token[:12] + "...")
