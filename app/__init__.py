import os
import sqlite3
from flask import Flask
from .utils import close_db, DATABASE
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.getenv("SECRET_KEY")

    app.config["CLICKSEND_USERNAME"] = os.getenv("CLICKSEND_USERNAME")
    app.config["CLICKSEND_API_KEY"] = os.getenv("CLICKSEND_API_KEY")

    # import blueprints here to avoid circular imports
    from .auth import auth_bp
    from .admin import admin_bp
    from .agent import agent_bp

    # register blueprints
    app.register_blueprint(auth_bp)  # auth routes
    app.register_blueprint(admin_bp, url_prefix="/admin")  # admin routes under /admin
    app.register_blueprint(agent_bp, url_prefix="/agent")  # agent routes under /agent

    # close db after each request
    app.teardown_appcontext(close_db)

    # ensure DB exists and tables are created
    create_database()

    # Initialize scheduler
    from .scheduler import schedule_rate_updates
    schedule_rate_updates(app)

    # ============ JINJA2 FILTERS ============
    def time_ago(value):
        """Calculate time ago from datetime"""
        if not value:
            return ""

        # Handle timedelta objects
        if isinstance(value, timedelta):
            seconds = int(value.total_seconds())
        # Handle string values
        elif isinstance(value, str):
            try:
                # Try to parse the string as datetime
                dt = string_to_datetime(value)
                if dt:
                    seconds = int((datetime.now() - dt).total_seconds())
                else:
                    return value
            except:
                return value
        else:
            return str(value)

        # Format the time ago
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

    def format_currency(value):
        """Format currency with commas"""
        if value is None:
            return "ZAR 0.00"
        try:
            return f"ZAR {float(value):,.2f}"
        except:
            return f"ZAR {value}"

    def format_date(value):
        """Format date nicely"""
        if not value:
            return ''
        try:
            dt = string_to_datetime(value)
            if dt:
                return dt.strftime('%d %b %Y %I:%M %p')
        except:
            pass
        return str(value)

    # Register all filters
    app.jinja_env.filters['time_ago'] = time_ago
    app.jinja_env.filters['string_to_datetime'] = string_to_datetime
    app.jinja_env.filters['format_currency'] = format_currency
    app.jinja_env.filters['format_date'] = format_date
    # NOTE: Using Jinja2's built-in 'truncate' filter, not custom one

    return app

def create_database():
    db_path = DATABASE
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # USERS TABLE
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT,
            username TEXT UNIQUE,
            password TEXT,
            phone TEXT,
            email TEXT,
            role TEXT,
            branch_id INTEGER,
            status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # BRANCHES TABLE
    cur.execute("""
        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            rate_override REAL
        )
    """)

    # AGENTS TABLE (link)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            branch_id INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)

    # CURRENCIES
    cur.execute("""
        CREATE TABLE IF NOT EXISTS currencies (
            code TEXT PRIMARY KEY,
            name TEXT
        )
    """)

    # EXCHANGE RATES & SETTINGS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_currency TEXT NOT NULL,
            to_currency TEXT NOT NULL,
            rate REAL NOT NULL,
            source TEXT DEFAULT 'manual',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    #TRANSACTIONS TABLE
    # - Update this in create_database() function
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT UNIQUE,
            sender_name TEXT,
            sender_phone TEXT,
            receiver_name TEXT,
            receiver_phone TEXT,
            amount_local REAL,
            amount_foreign REAL,
            currency_code TEXT,
            status TEXT DEFAULT 'pending',
            created_by INTEGER,
            completed_by INTEGER,
            verified_by INTEGER,
            agent_id INTEGER,
            branch_id INTEGER,
            token TEXT,
            available_to_all INTEGER DEFAULT 0,      -- WAS MISSING
            picked_by INTEGER,                       -- WAS MISSING
            picked_at DATETIME,                      -- WAS MISSING
            completed_at DATETIME,
            verified_at DATETIME,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            payment_method TEXT DEFAULT 'cash',
            notes TEXT,
            FOREIGN KEY(created_by) REFERENCES users(id),
            FOREIGN KEY(completed_by) REFERENCES users(id),
            FOREIGN KEY(verified_by) REFERENCES users(id),
            FOREIGN KEY(agent_id) REFERENCES users(id),
            FOREIGN KEY(branch_id) REFERENCES branches(id),
            FOREIGN KEY(picked_by) REFERENCES users(id)
        )
    """)

    # LOGS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # NOTIFICATIONS TABLE (optional but recommended)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # seeds
    cur.execute("INSERT OR IGNORE INTO currencies (code, name) VALUES (?, ?)", ("USD", "US Dollar"))
    cur.execute("INSERT OR IGNORE INTO currencies (code, name) VALUES (?, ?)", ("ZAR", "South African Rand"))
    cur.execute("""
        INSERT INTO exchange_rates (from_currency, to_currency, rate, source)
        SELECT ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM exchange_rates WHERE from_currency=? AND to_currency=?)
    """, ("USD", "ZAR", 18.50, "initial", "USD", "ZAR"))

    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("auto_update_rates", "true"))
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("last_rate_fetch", ""))

    # default admin
    cur.execute("SELECT 1 FROM users WHERE username = ?", ("admin",))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (full_name, username, password, role) VALUES (?, ?, ?, ?)",
                    ("Admin User", "admin", "admin123", "admin"))

    conn.commit()
    conn.close()