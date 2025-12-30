# init_db.py
import os
import sys

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import User, Currency, ExchangeRate, Setting


def init_database():
    app = create_app()

    with app.app_context():
        print("Dropping all tables...")
        db.drop_all()

        print("Creating all tables...")
        db.create_all()
        print("✅ Tables created successfully!")

        # Seed data
        print("Seeding database...")

        # Create currencies
        currencies = [
            Currency(code="USD", name="US Dollar"),
            Currency(code="ZAR", name="South African Rand"),
            Currency(code="EUR", name="Euro"),
            Currency(code="GBP", name="British Pound")
        ]
        for currency in currencies:
            if not Currency.query.filter_by(code=currency.code).first():
                db.session.add(currency)
                print(f"  Added currency: {currency.code}")

        # Create initial exchange rate
        if not ExchangeRate.query.filter_by(from_currency="USD", to_currency="ZAR").first():
            rate = ExchangeRate(
                from_currency="USD",
                to_currency="ZAR",
                rate=18.50,
                source="initial"
            )
            db.session.add(rate)
            print("  Added exchange rate: USD → ZAR = 18.50")

        # Create settings
        settings = [
            Setting(key="auto_update_rates", value="true"),
            Setting(key="last_rate_fetch", value="")
        ]
        for setting in settings:
            if not Setting.query.filter_by(key=setting.key).first():
                db.session.add(setting)
                print(f"  Added setting: {setting.key}")

        # Create default admin user
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
            print("  Added admin user: admin / admin123")

        try:
            db.session.commit()
            print("✅ Database seeded successfully!")
            print("\nLogin credentials:")
            print("Username: admin")
            print("Password: admin123")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error seeding database: {e}")
            raise


if __name__ == "__main__":
    init_database()