from flask_login import UserMixin

from . import db
from datetime import datetime


class User(db.Model, UserMixin):  # Add UserMixin
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100))
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(100))
    role = db.Column(db.String(20), nullable=False)
    branch_id = db.Column(db.Integer)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Flask-Login required methods (simplified by UserMixin)
    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.status == 'active'

    @property
    def is_authenticated(self):
        return True  # Assuming if user object exists, they're authenticated

    @property
    def is_anonymous(self):
        return False

class Branch(db.Model):
    __tablename__ = 'branches'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(200))
    rate_override = db.Column(db.Float)


class Agent(db.Model):
    __tablename__ = 'agents'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'))


class Currency(db.Model):
    __tablename__ = 'currencies'

    code = db.Column(db.String(3), primary_key=True)
    name = db.Column(db.String(50))


class ExchangeRate(db.Model):
    __tablename__ = 'exchange_rates'

    id = db.Column(db.Integer, primary_key=True)
    from_currency = db.Column(db.String(3), nullable=False)
    to_currency = db.Column(db.String(3), nullable=False)
    rate = db.Column(db.Float, nullable=False)
    source = db.Column(db.String(20), default='manual')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Setting(db.Model):
    __tablename__ = 'settings'

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(50), unique=True, nullable=False)
    sender_name = db.Column(db.String(255), nullable=False)
    sender_phone = db.Column(db.String(20))
    receiver_name = db.Column(db.String(255), nullable=False)
    receiver_phone = db.Column(db.String(20))
    amount_local = db.Column(db.Float, nullable=False)
    amount_foreign = db.Column(db.Float, nullable=False)
    currency_code = db.Column(db.String(3))
    status = db.Column(db.String(20), default='pending')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    completed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    verified_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    agent_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'))
    token = db.Column(db.String(100))
    available_to_all = db.Column(db.Boolean, default=False)
    picked_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    picked_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    verified_at = db.Column(db.DateTime)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    payment_method = db.Column(db.String(50), default='cash')
    notes = db.Column(db.Text)


class Log(db.Model):
    __tablename__ = 'logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(100))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text)
    link = db.Column(db.String(200))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DollarBalance(db.Model):
    __tablename__ = 'dollar_balance'

    id = db.Column(db.Integer, primary_key=True)
    current_balance = db.Column(db.Float, default=0.00)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)