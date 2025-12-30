import os
from urllib.parse import urlparse

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "mysecret123")

    # Get DATABASE_URL from Railway environment variable
    DATABASE_URL = os.environ.get("DATABASE_URL")

    if DATABASE_URL:
        # Handle Railway's PostgreSQL URL format
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        # Fallback to SQLite for local development
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "hawala.db")

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # PostgreSQL connection pool settings (optional but recommended for production)
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_recycle': 300,
        'pool_pre_ping': True,
    }