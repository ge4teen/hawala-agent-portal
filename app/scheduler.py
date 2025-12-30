# app/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from flask import Flask
import atexit

scheduler = BackgroundScheduler()


def schedule_rate_updates(app):
    """Schedule automatic rate updates"""
    from .rates import update_usd_zar

    def update_job():
        with app.app_context():
            # Check if auto-update is enabled
            from .utils import get_setting
            auto_update = get_setting("auto_update_rates") or "true"
            if auto_update == "true":
                update_usd_zar()

    # Schedule job to run every hour
    scheduler.add_job(
        func=update_job,
        trigger=IntervalTrigger(hours=1),
        id='rate_update_job',
        name='Update exchange rates hourly',
        replace_existing=True
    )

    scheduler.start()

    # Shut down scheduler when app exits
    atexit.register(lambda: scheduler.shutdown())