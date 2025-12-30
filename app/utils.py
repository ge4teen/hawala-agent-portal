import os
from flask import g, session, redirect
from functools import wraps
from datetime import datetime, timedelta
import requests

# Remove SQLite imports and add SQLAlchemy
from . import db
from .models import Setting, ExchangeRate, DollarBalance, User


def get_current_user():
    """Get current user from session"""
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    return None


def require_role(role):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user or user.role != role:
                return redirect("/login")
            return f(*args, **kwargs)

        return wrapped

    return decorator


# --- settings & rate helpers ---

def get_setting(key):
    """Get setting value"""
    setting = Setting.query.filter_by(key=key).first()
    return setting.value if setting else None


def set_setting(key, value):
    """Set setting value"""
    setting = Setting.query.filter_by(key=key).first()
    if setting:
        setting.value = str(value)
        setting.updated_at = datetime.utcnow()
    else:
        setting = Setting(key=key, value=str(value))
        db.session.add(setting)
    db.session.commit()


def get_latest_rate(from_currency="USD", to_currency="ZAR"):
    """Get latest exchange rate"""
    rate = ExchangeRate.query.filter_by(
        from_currency=from_currency,
        to_currency=to_currency
    ).order_by(ExchangeRate.updated_at.desc()).first()

    if rate:
        return {
            "rate": rate.rate,
            "updated_at": rate.updated_at.isoformat() if rate.updated_at else None
        }
    return None


def fetch_rate_from_api(from_currency="USD", to_currency="ZAR"):
    """Fetch rate from external API"""
    url = f"https://api.exchangerate.host/latest?base={from_currency}&symbols={to_currency}"
    resp = requests.get(url, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    if "rates" in data and to_currency in data["rates"]:
        return float(data["rates"][to_currency])
    raise ValueError("Rate not found in API response")


def update_rate_if_needed(force=False, max_age_minutes=60):
    """Update exchange rate if needed"""
    auto = get_setting("auto_update_rates") or "true"
    auto_flag = (auto.lower() == "true")

    if not auto_flag and not force:
        return get_latest_rate()

    latest = get_latest_rate()
    if latest and not force:
        try:
            last_updated = datetime.fromisoformat(latest["updated_at"])
            if datetime.utcnow() - last_updated < timedelta(minutes=max_age_minutes):
                return latest
        except Exception:
            pass

    try:
        rate = fetch_rate_from_api("USD", "ZAR")

        # Create new rate entry
        new_rate = ExchangeRate(
            from_currency="USD",
            to_currency="ZAR",
            rate=rate,
            source="api",
            updated_at=datetime.utcnow()
        )
        db.session.add(new_rate)
        db.session.commit()

        set_setting("last_rate_fetch", datetime.utcnow().isoformat())

        return {
            "rate": rate,
            "updated_at": datetime.utcnow().isoformat()
        }
    except Exception:
        return latest


def get_dollar_balance():
    """Get current dollar balance"""
    balance = DollarBalance.query.first()
    if not balance:
        balance = DollarBalance(current_balance=0.00)
        db.session.add(balance)
        db.session.commit()
    return balance


def update_dollar_balance(new_balance):
    """Update dollar balance"""
    balance = DollarBalance.query.first()
    if not balance:
        balance = DollarBalance(current_balance=new_balance)
    else:
        balance.current_balance = new_balance
        balance.last_updated = datetime.utcnow()

    db.session.add(balance)
    db.session.commit()
    return balance


# Time formatting functions (keep these as they're fine)
def time_ago(value):
    """Calculate time ago from datetime"""
    if not value:
        return ""

    try:
        # Handle timedelta objects
        if isinstance(value, timedelta):
            seconds = int(value.total_seconds())
        # Handle datetime objects
        elif isinstance(value, datetime):
            seconds = int((datetime.utcnow() - value).total_seconds())
        # Handle string values
        elif isinstance(value, str):
            dt = string_to_datetime(value)
            if dt:
                seconds = int((datetime.utcnow() - dt).total_seconds())
            else:
                return str(value)
        else:
            return str(value)

        if seconds < 0:
            return "in the future"
        elif seconds < 60:
            return f"{seconds} seconds ago"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        else:
            days = seconds // 86400
            if days < 30:
                return f"{days} day{'s' if days > 1 else ''} ago"
            elif days < 365:
                months = days // 30
                return f"{months} month{'s' if months > 1 else ''} ago"
            else:
                years = days // 365
                return f"{years} year{'s' if years > 1 else ''} ago"

    except Exception:
        return str(value)


def string_to_datetime(value):
    """Convert string to datetime object"""
    if not value:
        return None

    # Remove microseconds if present
    if '.' in value:
        value = value.split('.')[0]

    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%d/%m/%Y %H:%M:%S',
        '%d/%m/%Y %H:%M',
        '%d/%m/%Y'
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None