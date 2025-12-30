import requests
from datetime import datetime, timedelta
from flask import current_app
from .models import db, ExchangeRate, Setting
from sqlalchemy import desc, func


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
        # Insert new rate
        exchange_rate = ExchangeRate(
            from_currency="USD",
            to_currency="ZAR",
            rate=float(rate),
            source=source,
            updated_at=datetime.utcnow()
        )

        db.session.add(exchange_rate)
        db.session.commit()

        # Keep only last 100 rates to prevent database bloat
        # Get IDs of the 100 most recent rates
        recent_ids = db.session.query(ExchangeRate.id).order_by(
            ExchangeRate.updated_at.desc()
        ).limit(100).subquery()

        # Delete older rates (PostgreSQL-compatible syntax)
        db.session.execute(
            db.delete(ExchangeRate).where(
                ExchangeRate.id.not_in(db.select(recent_ids.c.id))
            )
        )
        db.session.commit()

        return {"ok": True, "rate": rate, "source": source}

    except Exception as e:
        current_app.logger.error(f"Error saving rate to DB: {str(e)}")
        return {"ok": False, "error": str(e)}


def should_update_rates():
    """Check if rates should be updated automatically"""
    # Check auto-update setting
    setting = Setting.query.filter_by(key='auto_update_rates').first()

    if setting and setting.value == 'false':
        return False

    # Check when rates were last updated
    # Using func.max to get the maximum updated_at
    last_update_result = db.session.query(
        func.max(ExchangeRate.updated_at).label('last_update')
    ).filter_by(from_currency='USD', to_currency='ZAR').first()

    if not last_update_result or not last_update_result.last_update:
        return True  # Never updated, needs update

    # Calculate time difference
    last_update = last_update_result.last_update
    now = datetime.utcnow()

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
    rate = ExchangeRate.query.filter_by(
        from_currency='USD',
        to_currency='ZAR'
    ).order_by(ExchangeRate.updated_at.desc()).first()

    if rate:
        return {
            'rate': rate.rate,
            'source': rate.source,
            'updated_at': rate.updated_at
        }
    return None