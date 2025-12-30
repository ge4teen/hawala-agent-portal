# app/helpers.py
import secrets, time, sqlite3
from .utils import get_db

def generate_txid():
    # e.g. ISA-8HEX-6TS (uppercase)
    rand = secrets.token_hex(4).upper()  # 8 hex
    ts = str(int(time.time()))[-6:]     # last 6 digits of timestamp
    return f"ISA-{rand}{ts}"

def generate_unique_txid(retries=6):
    db = get_db()
    cur = db.cursor()
    for _ in range(retries):
        txid = generate_txid()
        cur.execute("SELECT 1 FROM transactions WHERE transaction_id=?", (txid,))
        if not cur.fetchone():
            return txid
    raise RuntimeError("Unable to generate unique transaction id after retries")
