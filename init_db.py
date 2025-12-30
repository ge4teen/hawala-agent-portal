import os
import sys
sys.path.append('.')

from app import create_app, db
from app.models import DollarBalance, User, Transaction

app = create_app()

with app.app_context():
    # Create all tables
    db.create_all()
    
    # Create initial dollar balance if not exists
    if not DollarBalance.query.first():
        balance = DollarBalance(current_balance=0.00)
        db.session.add(balance)
        db.session.commit()
        print("Created initial dollar balance")
    
    print("Database initialized successfully")
