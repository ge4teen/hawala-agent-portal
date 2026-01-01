import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from datetime import datetime

# Only load dotenv in local development, not on Railway
if not os.environ.get("RAILWAY_ENVIRONMENT") and not os.environ.get("RAILWAY_PROJECT_NAME"):
    from dotenv import load_dotenv

    load_dotenv()
    print("📝 Loaded .env file for local development")

# Initialize extensions at module level
db = SQLAlchemy()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")

    # ============ DEBUG: ENVIRONMENT CHECK ============
    print(f"\n{'=' * 60}")
    print("🔍 ENVIRONMENT DIAGNOSTICS")
    print(f"{'=' * 60}")

    # Check if we're on Railway
    is_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_NAME"))
    print(f"Running on Railway: {is_railway}")

    # Check DATABASE_URL specifically
    database_url = os.environ.get("DATABASE_URL")
    print(f"DATABASE_URL found: {'YES' if database_url else 'NO'}")

    # List all database-related environment variables
    print("\n📋 Database-related environment variables:")
    db_vars_found = False
    for key in sorted(os.environ.keys()):
        key_lower = key.lower()
        if any(db_term in key_lower for db_term in ['database', 'postgres', 'pg', 'sql']):
            value = os.environ[key]
            # Mask passwords for security
            if 'pass' in key_lower or 'pwd' in key_lower:
                value = '********'
            print(f"  {key}: {value}")
            db_vars_found = True

    if not db_vars_found:
        print("  ❌ No database environment variables found!")

    print(f"{'=' * 60}\n")
    # ============ END DEBUG ============

    # Secret key
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

    # ClickSend config
    app.config["CLICKSEND_USERNAME"] = os.environ.get("CLICKSEND_USERNAME")
    app.config["CLICKSEND_API_KEY"] = os.environ.get("CLICKSEND_API_KEY")

    # Database configuration - CRITICAL FIX
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        print("❌ DATABASE_URL not found in os.environ")

        # On Railway, this is a critical error
        if is_railway:
            raise RuntimeError(
                "DATABASE_URL not set on Railway. Please ensure:\n"
                "1. PostgreSQL service is added to your project\n"
                "2. Services are connected in Railway dashboard\n"
                "3. Web service shows PostgreSQL as 'Connected'"
            )
        else:
            # Local development fallback
            app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
            print("⚠️ Using SQLite for local development")
    else:
        # Fix for Railway's PostgreSQL URL format
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
            print("✅ Fixed PostgreSQL URL format (postgres:// → postgresql://)")

        # Add SSL mode for Railway PostgreSQL
        if 'postgresql' in database_url and 'sslmode' not in database_url:
            if '?' in database_url:
                database_url += '&sslmode=require'
            else:
                database_url += '?sslmode=require'
            print("✅ Added SSL mode to PostgreSQL URL")

        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
        print(f"✅ Using PostgreSQL database")
        print(f"   Connection: {database_url.split('@')[-1].split('?')[0]}")

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_recycle': 300,
        'pool_pre_ping': True,
    }

    # Initialize extensions with app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    # ===== USER LOADER =====
    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        try:
            return User.query.get(int(user_id))
        except:
            return None

    # ===== REST OF YOUR CODE (jinja filters, context processors, blueprints) =====
    # ... (keep all your existing jinja filters, context processors, etc) ...

    # Register blueprints
    from .auth import auth_bp
    from .admin import admin_bp
    from .agent import agent_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(agent_bp, url_prefix="/agent")

    # ===== DATABASE SETUP WITH CONDITIONAL SEEDING =====
    with app.app_context():
        try:
            print("🛠️ Creating database tables if they don't exist...")
            db.create_all()
            print("✅ Database tables ready")

            # REMOVE seed_database() from here!
            # This is causing your data reset
            # Move to a separate script or add condition

            # Only seed if no users exist (first-time setup)
            from .models import User
            if not User.query.first():
                print("🌱 First-time setup: Seeding database...")
                seed_database()
            else:
                print("📊 Database already has data, skipping seed")

        except Exception as e:
            print(f"❌ Error during database setup: {e}")
            import traceback
            traceback.print_exc()

    # ===== REMOVE OR MODIFY SEED_DATABASE FUNCTION =====
    # Your seed_database() function should ONLY run on first setup
    # Not on every app restart

    print(f"\n{'=' * 60}")
    print("🚀 Application initialized successfully")
    if is_railway:
        print("🌐 Running on Railway with PostgreSQL")
    else:
        print("💻 Running locally with SQLite")
    print(f"{'=' * 60}")

    return app


def seed_database():
    """Seed initial data - ONLY RUNS ONCE"""
    from .models import Currency, ExchangeRate, Setting, User, DollarBalance

    try:
        # ... your seed logic ...
        # IMPORTANT: Add checks to prevent re-seeding

        # Check if admin already exists
        if User.query.filter_by(username="admin").first():
            print("  ⏩ Admin user already exists, skipping seed")
            return

        # ... rest of your seed code ...

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error seeding database: {e}")
        raise