# app/rates.py
import requests
import sqlite3
from datetime import datetime, timedelta
from flask import current_app
import time


def get_db():
    """Get database connection"""
    from .utils import get_db
    return get_db()


def update_usd_zar():
    """
    Fetch latest USD to ZAR rate from an API and update database
    Returns: dict with 'ok', 'rate', 'error'
    """
    try:
        # Try multiple free exchange rate APIs (fallback method)
        rate = None

        # API 1: ExchangeRate-API (free tier)
        try:
            response = requests.get(
                "https://api.exchangerate-api.com/v4/latest/USD",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                rate = data['rates'].get('ZAR')
                if rate:
                    return save_rate_to_db(rate, "ExchangeRate-API")
        except Exception as e:
            current_app.logger.debug(f"API 1 failed: {e}")

        # API 2: Frankfurter (free, no API key needed)
        try:
            response = requests.get(
                "https://api.frankfurter.app/latest?from=USD&to=ZAR",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                rate = data['rates'].get('ZAR')
                if rate:
                    return save_rate_to_db(rate, "Frankfurter")
        except Exception as e:
            current_app.logger.debug(f"API 2 failed: {e}")

        # API 3: Open Exchange Rates (fallback, needs app_id in config)
        if current_app.config.get('OPENEXCHANGE_API_KEY'):
            try:
                app_id = current_app.config['OPENEXCHANGE_API_KEY']
                response = requests.get(
                    f"https://openexchangerates.org/api/latest.json?app_id={app_id}&symbols=ZAR",
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    rate = data['rates'].get('ZAR')
                    if rate:
                        return save_rate_to_db(rate, "OpenExchangeRates")
            except Exception as e:
                current_app.logger.debug(f"API 3 failed: {e}")

        # API 4: CurrencyLayer (fallback, needs access_key in config)
        if current_app.config.get('CURRENCYLAYER_API_KEY'):
            try:
                access_key = current_app.config['CURRENCYLAYER_API_KEY']
                response = requests.get(
                    f"http://api.currencylayer.com/live?access_key={access_key}&currencies=ZAR&source=USD",
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('success'):
                        rate = data['quotes'].get('USDZAR')
                        if rate:
                            return save_rate_to_db(rate, "CurrencyLayer")
            except Exception as e:
                current_app.logger.debug(f"API 4 failed: {e}")

        # If all APIs fail, use a fixed fallback rate
        if not rate:
            current_app.logger.warning("All exchange rate APIs failed, using fallback rate")
            rate = 18.50  # Conservative fallback rate
            return save_rate_to_db(rate, "Fallback")

        return {"ok": False, "error": "Could not fetch rate from any source"}

    except Exception as e:
        current_app.logger.error(f"Error fetching exchange rate: {str(e)}")
        return {"ok": False, "error": str(e)}


def save_rate_to_db(rate, source):
    """Save rate to database"""
    try:
        db = get_db()
        cur = db.cursor()

        # Insert new rate
        cur.execute("""
            INSERT INTO exchange_rates (from_currency, to_currency, rate, source, updated_at) 
            VALUES (?, ?, ?, ?, datetime('now'))
        """, ("USD", "ZAR", float(rate), source))

        db.commit()

        # Keep only last 100 rates to prevent database bloat
        cur.execute("""
            DELETE FROM exchange_rates 
            WHERE id NOT IN (
                SELECT id FROM exchange_rates 
                ORDER BY updated_at DESC 
                LIMIT 100
            )
        """)
        db.commit()

        return {"ok": True, "rate": rate, "source": source}

    except Exception as e:
        current_app.logger.error(f"Error saving rate to DB: {str(e)}")
        return {"ok": False, "error": str(e)}


def should_update_rates():
    """Check if rates should be updated automatically"""
    db = get_db()
    cur = db.cursor()

    # Check auto-update setting
    cur.execute("SELECT value FROM settings WHERE key='auto_update_rates'")
    setting = cur.fetchone()

    if setting and setting['value'] == 'false':
        return False

    # Check when rates were last updated
    cur.execute("SELECT MAX(updated_at) as last_update FROM exchange_rates")
    result = cur.fetchone()

    if not result or not result['last_update']:
        return True  # Never updated, needs update

    # Parse the last update time
    last_update = datetime.strptime(result['last_update'], '%Y-%m-%d %H:%M:%S')
    now = datetime.now()

    # Update if more than 1 hour has passed (markets update frequently)
    time_diff = now - last_update
    return time_diff.total_seconds() > 3600  # 1 hour


def update_rate_if_needed(force=False):
    """Update rates if needed, returns latest rate"""
    if force or should_update_rates():
        result = update_usd_zar()
        if result.get('ok'):
            current_app.logger.info(f"Rates updated: {result['rate']} from {result.get('source', 'unknown')}")
        else:
            current_app.logger.warning(f"Failed to update rates: {result.get('error')}")

    # Return latest rate regardless
    return get_latest_rate()


def get_latest_rate():
    """Get the latest exchange rate"""
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT rate, source, updated_at 
        FROM exchange_rates 
        WHERE from_currency='USD' AND to_currency='ZAR' 
        ORDER BY updated_at DESC 
        LIMIT 1
    """)
    return cur.fetchone()