import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from dotenv import load_dotenv
from datetime import datetime

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

        # Add SSL mode for Railway PostgreSQL
        if 'postgresql' in database_url and 'sslmode' not in database_url:
            database_url += '?sslmode=require'

        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
        print(f"✅ Using PostgreSQL database")
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
        print(f"⚠️ Using SQLite database (for development only)")

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
    def truncate_filter(value, length=50, end='...'):
        """Truncate a string"""
        if not value:
            return ''
        value_str = str(value)
        if len(value_str) <= length:
            return value_str
        return value_str[:length] + end

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
            print("🛠️ Creating database tables...")
            db.create_all()
            print("✅ Database tables created")

            # Seed initial data
            print("🌱 Seeding database...")
            seed_database()

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

    print(f"🚀 Application initialized successfully")
    return app


def seed_database():
    """Seed initial data"""
    from .models import Currency, ExchangeRate, Setting, User, DollarBalance

    try:
        # Create currencies
        currencies_data = [
            ('USD', 'US Dollar', '$'),
            ('ZAR', 'South African Rand', 'R'),
            ('EUR', 'Euro', '€'),
            ('GBP', 'British Pound', '£'),
        ]

        for code, name, symbol in currencies_data:
            if not Currency.query.filter_by(code=code).first():
                currency = Currency(code=code, name=name)
                db.session.add(currency)
                print(f"  Created currency: {code}")

        # Create initial exchange rate
        if not ExchangeRate.query.filter_by(from_currency="USD", to_currency="ZAR").first():
            rate = ExchangeRate(
                from_currency="USD",
                to_currency="ZAR",
                rate=18.50,
                source="initial"
            )
            db.session.add(rate)
            print("  Created exchange rate: USD → ZAR = 18.50")

        # Create settings
        settings_data = [
            ('auto_update_rates', 'true'),
            ('last_rate_fetch', ''),
            ('system_name', 'Hawala Exchange System'),
            ('default_currency', 'ZAR'),
            ('exchange_rate_margin', '0.02'),
        ]

        for key, value in settings_data:
            if not Setting.query.filter_by(key=key).first():
                setting = Setting(key=key, value=value)
                db.session.add(setting)
                print(f"  Created setting: {key} = {value}")

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
            print("  Created admin user: admin / admin123")

        # Create initial dollar balance
        if not DollarBalance.query.first():
            balance = DollarBalance(current_balance=0.00)
            db.session.add(balance)
            print("  Created initial dollar balance: $0.00")

        db.session.commit()
        print("✅ Database seeded successfully")

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error seeding database: {e}")
        import traceback
        traceback.print_exc()
        raise