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

# ✅ NEW: Import SNS client (initialization happens in create_app)



def create_app():
    from aws_sns import sns_client
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
            if 'pass' in key_lower or 'pwd' in key_lower or 'secret' in key_lower:
                value = '********'
            print(f"  {key}: {value}")
            db_vars_found = True

    if not db_vars_found:
        print("  ❌ No database environment variables found!")

    # ✅ NEW: Check AWS SNS Configuration
    print("\n📡 AWS SNS Configuration Check:")
    aws_keys_found = all([
        os.environ.get("AWS_ACCESS_KEY_ID"),
        os.environ.get("AWS_SECRET_ACCESS_KEY"),
        os.environ.get("AWS_REGION"),
        os.environ.get("AWS_SNS_TOPIC_ARN")
    ])

    if aws_keys_found:
        print("  ✅ All AWS SNS environment variables found")
        print(f"  Region: {os.environ.get('AWS_REGION')}")
        print(f"  Topic ARN: {os.environ.get('AWS_SNS_TOPIC_ARN')[:50]}...")
    else:
        print("  ⚠️ AWS SNS environment variables missing or incomplete")
        print("  SNS notifications will be disabled")

    print(f"{'=' * 60}\n")
    # ============ END DEBUG ============

    # Secret key
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

    # Debug: Check if secret key is set
    if app.secret_key == "dev-secret-key":
        print("⚠️ WARNING: Using default secret key. Set SECRET_KEY environment variable.")

    # ClickSend config
    app.config["CLICKSEND_USERNAME"] = os.environ.get("CLICKSEND_USERNAME")
    app.config["CLICKSEND_API_KEY"] = os.environ.get("CLICKSEND_API_KEY")

    # ✅ NEW: AWS SNS Configuration
    app.config["AWS_ACCESS_KEY_ID"] = os.environ.get("AWS_ACCESS_KEY_ID")
    app.config["AWS_SECRET_ACCESS_KEY"] = os.environ.get("AWS_SECRET_ACCESS_KEY")
    app.config["AWS_REGION"] = os.environ.get("AWS_REGION", "af-south-1")
    app.config["AWS_SNS_TOPIC_ARN"] = os.environ.get("AWS_SNS_TOPIC_ARN")

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

    # ✅ NEW: Initialize SNS Client
    try:
        sns_client.init_app(app)
        print("✅ AWS SNS client initialized")
    except Exception as e:
        print(f"⚠️ Failed to initialize SNS client: {e}")
        print("   SNS notifications will be disabled")

    # ===== CRITICAL: ADD USER LOADER FOR FLASK-LOGIN =====
    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        try:
            return User.query.get(int(user_id))
        except:
            return None

    # ============ JINJA2 FILTERS ============
    # These filters are used in templates

    @app.template_filter('format_date')
    def format_date_filter(value, format_string='%Y-%m-%d %H:%M:%S'):
        """Format a datetime object or string"""
        if value is None:
            return ''

        # If it's already a datetime object
        if isinstance(value, datetime):
            return value.strftime(format_string)

        # If it's a string, try to parse it
        try:
            # Try common datetime formats
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y-%m-%d %H:%M:%S.%f'):
                try:
                    dt = datetime.strptime(str(value), fmt)
                    return dt.strftime(format_string)
                except ValueError:
                    continue
            # If none work, return original
            return str(value)
        except:
            return str(value)

    @app.template_filter('format_currency')
    def format_currency_filter(value, currency='ZAR'):
        """Format a number as currency"""
        if value is None:
            return f'0.00'
        try:
            # For ZAR (South African Rand)
            if currency == 'ZAR':
                return f'R {float(value):,.2f}'
            # For USD
            elif currency == 'USD':
                return f'$ {float(value):,.2f}'
            # Default
            else:
                return f'{float(value):,.2f} {currency}'
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter('format_number')
    def format_number_filter(value):
        """Format a number with commas"""
        if value is None:
            return '0'
        try:
            return f'{float(value):,.0f}'
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter('format_float')
    def format_float_filter(value, decimals=2):
        """Format a float with specified decimals"""
        if value is None:
            return f'0.{"0" * decimals}'
        try:
            return f'{float(value):,.{decimals}f}'
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter('truncate')
    def truncate_filter(value, length=50, killwords=False, end='...'):
        """Truncate a string with optional killwords parameter"""
        if not value:
            return ''

        value_str = str(value)

        # If string is already shorter than or equal to length, return as-is
        if len(value_str) <= length:
            return value_str

        # If killwords is True or killwords is passed as a string (legacy support)
        if killwords is True or (isinstance(killwords, str) and killwords.lower() == 'true'):
            # Truncate exactly at length
            return value_str[:length] + end

        # Handle case where killwords might be passed as end parameter (backward compatibility)
        if isinstance(killwords, str) and killwords not in ('true', 'false', 'True', 'False'):
            # This means killwords was actually passed as the 'end' parameter
            return value_str[:length] + killwords

        # Otherwise, try to truncate at word boundary
        truncated = value_str[:length]
        last_space = truncated.rfind(' ')

        if last_space > 0:
            return truncated[:last_space] + end
        else:
            return truncated + end

    @app.template_filter('yesno')
    def yesno_filter(value):
        """Convert boolean to Yes/No"""
        if value in (True, 'true', 'True', '1', 1):
            return 'Yes'
        return 'No'

    @app.template_filter('format_datetime')
    def format_datetime_filter(value):
        """Alias for format_date for compatibility"""
        return format_date_filter(value)

    @app.template_filter('time_ago')
    def time_ago_filter(value):
        """Show relative time (e.g., '2 hours ago')"""
        if not value:
            return ''

        if isinstance(value, str):
            try:
                value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            except:
                return str(value)

        if not isinstance(value, datetime):
            return str(value)

        now = datetime.utcnow()
        diff = now - value

        if diff.days > 365:
            years = diff.days // 365
            return f'{years} year{"s" if years > 1 else ""} ago'
        elif diff.days > 30:
            months = diff.days // 30
            return f'{months} month{"s" if months > 1 else ""} ago'
        elif diff.days > 0:
            return f'{diff.days} day{"s" if diff.days > 1 else ""} ago'
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f'{hours} hour{"s" if hours > 1 else ""} ago'
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f'{minutes} minute{"s" if minutes > 1 else ""} ago'
        else:
            return 'just now'

    @app.template_filter('format_phone')
    def format_phone_filter(value):
        """Format phone number"""
        if not value:
            return ''
        phone = str(value).replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if len(phone) == 10 and phone.startswith('0'):
            return f'+27 {phone[1:4]} {phone[4:7]} {phone[7:]}'
        return phone

    @app.template_filter('format_percent')
    def format_percent_filter(value):
        """Format as percentage"""
        if value is None:
            return '0%'
        try:
            return f'{float(value):.1f}%'
        except:
            return str(value)

    # ============ CONTEXT PROCESSORS ============
    # Add variables to all templates

    @app.context_processor
    def inject_now():
        """Add current datetime to all templates"""
        return {'now': datetime.utcnow()}

    @app.context_processor
    def inject_config():
        """Add app config to all templates"""
        return {
            'app_name': 'Hawala Exchange',
            'version': '1.0.0',
        }

    @app.context_processor
    def inject_user():
        """Add user info to all templates"""
        from flask_login import current_user
        return {'current_user': current_user}

    # ✅ NEW: Add SNS status to templates (optional)
    @app.context_processor
    def inject_sns_status():
        """Add SNS status to templates"""
        return {
            'sns_enabled': sns_client.sns is not None and sns_client.topic_arn is not None
        }

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

            # Only seed if no users exist (first-time setup)
            from .models import User
            if not User.query.first():
                print("🌱 First-time setup: Seeding database...")
                seed_database()
            else:
                print("📊 Database already has data, skipping seed")
                # Just print how many users exist
                user_count = User.query.count()
                print(f"   Found {user_count} existing user(s)")

        except Exception as e:
            print(f"❌ Error during database setup: {e}")
            import traceback
            traceback.print_exc()

    # Initialize scheduler
    try:
        from .scheduler import schedule_rate_updates
        schedule_rate_updates(app)
        print("⏰ Scheduler initialized")
    except Exception as e:
        print(f"⚠️ Could not initialize scheduler: {e}")

    print(f"\n{'=' * 60}")
    print("🚀 Application initialized successfully")
    if is_railway:
        print("🌐 Running on Railway with PostgreSQL")
    else:
        print("💻 Running locally with SQLite")

    # ✅ NEW: SNS Status
    if sns_client.sns and sns_client.topic_arn:
        print("📡 AWS SNS: ✅ Enabled")
    else:
        print("📡 AWS SNS: ⚠️ Disabled (missing configuration)")

    print(f"{'=' * 60}")

    return app


def seed_database():
    """Seed initial data - ONLY RUNS ONCE"""
    from .models import Currency, ExchangeRate, Setting, User, DollarBalance

    try:
        print("  Starting database seeding...")

        # Create currencies
        currencies_data = [
            ('USD', 'US Dollar', '$'),
            ('ZAR', 'South African Rand', 'R'),
            ('EUR', 'Euro', '€'),
            ('GBP', 'British Pound', '£'),
        ]

        currencies_created = 0
        for code, name, symbol in currencies_data:
            if not Currency.query.filter_by(code=code).first():
                currency = Currency(code=code, name=name)
                db.session.add(currency)
                currencies_created += 1
                print(f"    Created currency: {code}")

        # Create initial exchange rate
        if not ExchangeRate.query.filter_by(from_currency="USD", to_currency="ZAR").first():
            rate = ExchangeRate(
                from_currency="USD",
                to_currency="ZAR",
                rate=18.50,
                source="initial"
            )
            db.session.add(rate)
            print("    Created exchange rate: USD → ZAR = 18.50")

        # Create settings
        settings_data = [
            ('auto_update_rates', 'true'),
            ('last_rate_fetch', ''),
            ('system_name', 'Hawala Exchange System'),
            ('default_currency', 'ZAR'),
            ('exchange_rate_margin', '0.02'),
            ('sns_notifications_enabled', 'true'),  # ✅ NEW: SNS setting
            ('low_balance_threshold', '1000'),  # ✅ NEW: Low balance threshold
        ]

        settings_created = 0
        for key, value in settings_data:
            if not Setting.query.filter_by(key=key).first():
                setting = Setting(key=key, value=value)
                db.session.add(setting)
                settings_created += 1
                print(f"    Created setting: {key} = {value}")

        # Create default admin user if not exists
        if not User.query.filter_by(username="admin").first():
            admin = User(
                full_name="Admin User",
                username="admin",
                password="admin123",  # TODO: Hash this in production!
                role="admin",
                status="active",
                email="admin@example.com"
            )
            db.session.add(admin)
            print("    Created admin user: admin / admin123")
        else:
            print("    Admin user already exists, skipping")

        # Create initial dollar balance
        if not DollarBalance.query.first():
            balance = DollarBalance(current_balance=10000.00)  # ✅ Start with $10,000
            db.session.add(balance)
            print("    Created initial dollar balance: $10,000.00")
        else:
            print("    Dollar balance already exists, skipping")

        db.session.commit()
        print(f"✅ Database seeded successfully")
        print(f"   Created: {currencies_created} currencies, {settings_created} settings")

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error seeding database: {e}")
        import traceback
        traceback.print_exc()
        raise