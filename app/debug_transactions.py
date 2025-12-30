# app/database_migrations/dollar_balance.py
import sqlite3
from pathlib import Path


def create_dollar_balance_tables():
    db_path = Path(__file__).parent.parent / "hawala.db"

    print(f"Creating dollar balance tables in: {db_path}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    try:
        # Main dollar balance table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dollar_balance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                current_balance REAL DEFAULT 0,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Balance history log table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dollar_balance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT,
                change_amount REAL NOT NULL,
                previous_balance REAL NOT NULL,
                new_balance REAL NOT NULL,
                change_type TEXT, -- 'transaction', 'manual_adjustment', 'initial'
                description TEXT,
                created_by INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id),
                FOREIGN KEY(created_by) REFERENCES users(id)
            )
        """)

        # Insert initial balance record if not exists
        cur.execute("INSERT OR IGNORE INTO dollar_balance (id, current_balance) VALUES (1, 0)")

        # Calculate initial balance from existing transactions
        print("Calculating initial balance from existing transactions...")
        cur.execute("SELECT SUM(amount_foreign) as total FROM transactions WHERE status='completed'")
        result = cur.fetchone()
        initial_balance = result['total'] if result and result['total'] else 0

        # Update initial balance
        cur.execute("UPDATE dollar_balance SET current_balance = ? WHERE id = 1", (initial_balance,))

        # Log the initial balance
        if initial_balance > 0:
            cur.execute("""
                INSERT INTO dollar_balance_log 
                (change_amount, previous_balance, new_balance, change_type, description)
                VALUES (?, ?, ?, ?, ?)
            """, (initial_balance, 0, initial_balance, 'initial',
                  'Initial balance from existing completed transactions'))

        conn.commit()
        print(f"✅ Dollar balance tables created successfully!")
        print(f"✅ Initial balance set to: ${initial_balance:.2f}")

    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating tables: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    create_dollar_balance_tables()