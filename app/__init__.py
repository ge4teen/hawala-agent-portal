import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from dotenv import load_dotenv

load_dotenv()

# Initialize extensions at module level
db = SQLAlchemy()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")

    # Secret key
    app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")

    # Debug: Check if secret key is set
    if app.secret_key == "dev-secret-key":
        print("⚠️ WARNING: Using default secret key. Set SECRET_KEY environment variable.")

    # ClickSend config
    app.config["CLICKSEND_USERNAME"] = os.getenv("CLICKSEND_USERNAME")
    app.config["CLICKSEND_API_KEY"] = os.getenv("CLICKSEND_API_KEY")

    # Database configuration
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Fix for Railway's PostgreSQL URL
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize extensions with app
    db.init_app(app)
    login_manager.init_app(app)

    # ===== CRITICAL: ADD USER LOADER FOR FLASK-LOGIN =====
    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        try:
            return User.query.get(int(user_id))
        except:
            return None

    # Register blueprints
    from .auth import auth_bp
    from .admin import admin_bp
    from .agent import agent_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(agent_bp, url_prefix="/agent")

    # Create database tables
    with app.app_context():
        try:
            db.create_all()
            print("✅ Database tables created")
            # Seed initial data
            seed_database()
        except Exception as e:
            print(f"❌ Error creating database: {e}")

    # Initialize scheduler
    try:
        from .scheduler import schedule_rate_updates
        schedule_rate_updates(app)
        print("✅ Scheduler initialized")
    except Exception as e:
        print(f"⚠️ Could not initialize scheduler: {e}")

    return app


def seed_database():
    """Seed initial data"""
    from .models import Currency, ExchangeRate, Setting, User

    try:
        # Create currencies
        currencies = [
            Currency(code="USD", name="US Dollar"),
            Currency(code="ZAR", name="South African Rand")
        ]
        for currency in currencies:
            if not Currency.query.filter_by(code=currency.code).first():
                db.session.add(currency)

        # Create initial exchange rate
        if not ExchangeRate.query.filter_by(from_currency="USD", to_currency="ZAR").first():
            rate = ExchangeRate(
                from_currency="USD",
                to_currency="ZAR",
                rate=18.50,
                source="initial"
            )
            db.session.add(rate)

        # Create settings
        settings = [
            Setting(key="auto_update_rates", value="true"),
            Setting(key="last_rate_fetch", value="")
        ]
        for setting in settings:
            if not Setting.query.filter_by(key=setting.key).first():
                db.session.add(setting)

        # Create default admin user if not exists
        if not User.query.filter_by(username="admin").first():
            admin = User(
                full_name="Admin User",
                username="admin",
                password="admin123",  # TODO: Hash this in production!
                role="admin",
                status="active",
                email="admin@example.com"  # Added missing field
            )
            db.session.add(admin)

        db.session.commit()
        print("✅ Database seeded successfully")

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error seeding database: {e}")
        raise