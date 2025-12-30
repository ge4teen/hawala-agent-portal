import sqlite3
import os
from flask import g, session, redirect
from functools import wraps
from datetime import datetime, timedelta

BASE = os.path.dirname(__file__)
DATABASE = os.path.join(BASE, "hawala.db")

def get_db():
    print(">>> USING DB:", DATABASE)
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def require_role(role):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if session.get("role") != role or not session.get("user_id"):
                return redirect("/login")
            return f(*args, **kwargs)
        return wrapped
    return decorator

# --- settings & rate helpers ---
import requests

def get_setting(key):
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    r = cur.fetchone()
    return r["value"] if r else None

def set_setting(key, value):
    db = get_db(); cur = db.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))", (key, str(value)))
    db.commit()

def get_latest_rate(from_currency="USD", to_currency="ZAR"):
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT rate, updated_at FROM exchange_rates WHERE from_currency=? AND to_currency=? ORDER BY updated_at DESC LIMIT 1", (from_currency, to_currency))
    r = cur.fetchone()
    return {"rate": r["rate"], "updated_at": r["updated_at"]} if r else None

def fetch_rate_from_api(from_currency="USD", to_currency="ZAR"):
    url = f"https://api.exchangerate.host/latest?base={from_currency}&symbols={to_currency}"
    resp = requests.get(url, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    if "rates" in data and to_currency in data["rates"]:
        return float(data["rates"][to_currency])
    raise ValueError("Rate not found in API response")

def update_rate_if_needed(force=False, max_age_minutes=60):
    auto = get_setting("auto_update_rates") or "true"
    auto_flag = (auto.lower() == "true")
    if not auto_flag and not force:
        return get_latest_rate()

    latest = get_latest_rate()
    if latest and not force:
        try:
            last = datetime.fromisoformat(latest["updated_at"])
            if datetime.utcnow() - last < timedelta(minutes=max_age_minutes):
                return latest
        except Exception:
            pass

    try:
        rate = fetch_rate_from_api("USD", "ZAR")
        db = get_db(); cur = db.cursor()
        cur.execute("INSERT INTO exchange_rates (from_currency, to_currency, rate, updated_at) VALUES (?, ?, ?, datetime('now'))", ("USD", "ZAR", rate))
        db.commit()
        set_setting("last_rate_fetch", datetime.utcnow().isoformat())
        return {"rate": rate, "updated_at": datetime.utcnow().isoformat()}
    except Exception:
        return latest


from datetime import datetime
import time


def time_ago(value):
    """Calculate time ago from datetime"""
    if not value:
        return ""

    try:
        # value is a timedelta object
        seconds = int(value.total_seconds())
    except:
        return str(value)

    if seconds < 60:
        return f"{seconds} seconds ago"
    elif seconds < 3600:
        return f"{seconds // 60} minutes ago"
    elif seconds < 86400:
        return f"{seconds // 3600} hours ago"
    else:
        return f"{seconds // 86400} days ago"


def string_to_datetime(value):
    """Convert string to datetime object"""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except:
        try:
            return datetime.strptime(value, '%Y-%m-%d')
        except:
            return None

