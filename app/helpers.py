import secrets
import time
from .models import db, Transaction


def generate_txid():
    # e.g. ISA-8HEX-6TS (uppercase)
    rand = secrets.token_hex(4).upper()  # 8 hex
    ts = str(int(time.time()))[-6:]  # last 6 digits of timestamp
    return f"ISA-{rand}{ts}"


def generate_unique_txid(retries=6):
    for _ in range(retries):
        txid = generate_txid()
        # Check if transaction ID already exists
        existing = Transaction.query.filter_by(transaction_id=txid).first()
        if not existing:
            return txid

    raise RuntimeError("Unable to generate unique transaction id after retries")