from .models import db, User, Transaction, Branch, Currency, ExchangeRate, Setting, DollarBalance, Log, Notification, Agent
from flask import current_app
import os


def init_db():
    """Initialize database with tables and default data"""

    # Create all tables
    db.create_all()
    print("Database tables created successfully.")

    # Create default admin user if it doesn't exist
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            password='admin123',  # In production, use hashed password!
            role='admin',
            full_name='Administrator',
            email='admin@example.com'
        )
        db.session.add(admin)
        print("Default admin user created.")

    # Create default currencies if needed
    default_currencies = [
        ('ZAR', 'South African Rand', 'R'),
        ('USD', 'United States Dollar', '$'),
        ('EUR', 'Euro', '€'),
        ('GBP', 'British Pound', '£'),
    ]

    for code, name, symbol in default_currencies:
        currency = Currency.query.filter_by(code=code).first()
        if not currency:
            currency = Currency(
                code=code,
                name=name,
                symbol=symbol
            )
            db.session.add(currency)

    # Create default settings if needed
    default_settings = [
        ('system_name', 'Hawala Exchange System'),
        ('sms_enabled', 'true'),
        ('default_currency', 'ZAR'),
        ('exchange_rate_margin', '0.02'),  # 2% margin
    ]

    for key, value in default_settings:
        setting = Setting.query.filter_by(key=key).first()
        if not setting:
            setting = Setting(
                key=key,
                value=value,
                description=f'Default {key.replace("_", " ")} setting'
            )
            db.session.add(setting)

    # Create default exchange rates if needed
    default_rates = [
        ('USD', 'ZAR', 18.5),  # Example rate: 1 USD = 18.5 ZAR
        ('EUR', 'ZAR', 20.0),  # Example rate: 1 EUR = 20.0 ZAR
        ('GBP', 'ZAR', 23.0),  # Example rate: 1 GBP = 23.0 ZAR
    ]

    for from_curr, to_curr, rate in default_rates:
        # Check if rate exists
        existing_rate = ExchangeRate.query.filter_by(
            from_currency=from_curr,
            to_currency=to_curr
        ).first()

        if not existing_rate:
            exchange_rate = ExchangeRate(
                from_currency=from_curr,
                to_currency=to_curr,
                rate=rate,
                is_active=True
            )
            db.session.add(exchange_rate)

    # Create default branch if none exists
    branch = Branch.query.first()
    if not branch:
        branch = Branch(
            name='Main Branch',
            location='Head Office',
            contact_phone='+1234567890',
            is_active=True
        )
        db.session.add(branch)

    # Initialize dollar balance if needed
    dollar_balance = DollarBalance.query.first()
    if not dollar_balance:
        dollar_balance = DollarBalance(
            balance=0.0,
            last_updated=db.func.now()
        )
        db.session.add(dollar_balance)

    try:
        db.session.commit()
        print("Default data initialized successfully.")

        # Log the initialization
        log = Log(
            user_id=admin.id if admin else None,
            action='system_init',
            details='Database initialized with default data'
        )
        db.session.add(log)
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        print(f"Error initializing database: {str(e)}")
        raise

    return True