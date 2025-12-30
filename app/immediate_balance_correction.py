import sqlite3
from pathlib import Path


def fix_dollar_balance():
    db_path = Path(__file__).parent / "hawala.db"

    print(f"Fixing dollar balance in: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        print("\n=== CURRENT BALANCE ANALYSIS ===")

        # Check current balance
        cur.execute("SELECT current_balance FROM dollar_balance WHERE id = 1")
        current_balance_row = cur.fetchone()
        current_balance = current_balance_row['current_balance'] if current_balance_row else 0
        print(f"Current balance in database: ${current_balance:.2f}")

        # Calculate what the balance SHOULD be based on transactions
        print("\n=== ANALYZING TRANSACTIONS ===")

        # Sum of all completed transactions (money sent out)
        cur.execute("""
            SELECT 
                SUM(amount_foreign) as total_sent,
                COUNT(*) as transaction_count
            FROM transactions 
            WHERE status = 'completed'
        """)
        completed = cur.fetchone()
        total_sent = completed['total_sent'] if completed and completed['total_sent'] else 0
        print(
            f"Total sent in completed transactions: ${total_sent:.2f} ({completed['transaction_count']} transactions)")

        # Sum of all pending transactions (money to be sent)
        cur.execute("""
            SELECT 
                SUM(amount_foreign) as total_pending,
                COUNT(*) as transaction_count
            FROM transactions 
            WHERE status = 'pending'
        """)
        pending = cur.fetchone()
        total_pending = pending['total_pending'] if pending and pending['total_pending'] else 0
        print(f"Total in pending transactions: ${total_pending:.2f} ({pending['transaction_count']} transactions)")

        # Calculate what starting balance should be
        # Starting balance = Current balance + Total sent + Total pending
        correct_balance = current_balance + total_sent + total_pending
        print(f"\n=== CORRECTION NEEDED ===")
        print(f"Current balance: ${current_balance:.2f}")
        print(f"Add back sent money: +${total_sent:.2f}")
        print(f"Add back pending money: +${total_pending:.2f}")
        print(f"Correct starting balance should be: ${correct_balance:.2f}")

        if correct_balance <= 0:
            print(f"\n⚠️  WARNING: Correct balance (${correct_balance:.2f}) is still negative or zero!")
            print("You may need to manually add funds to the system.")
            initial_funds = float(input("Enter initial funds to add (e.g., 5000): $") or "10000")
            correct_balance = initial_funds

        # Apply the correction
        correction_amount = correct_balance - current_balance
        print(f"\n=== APPLYING CORRECTION ===")
        print(f"Correction amount: ${correction_amount:.2f}")
        print(f"New balance will be: ${correct_balance:.2f}")

        confirmation = input(f"\nApply correction of ${correction_amount:.2f}? (yes/no): ").lower()

        if confirmation == 'yes':
            # Update the balance
            cur.execute("""
                UPDATE dollar_balance 
                SET current_balance = ?, last_updated = datetime('now')
                WHERE id = 1
            """, (correct_balance,))

            # Log the correction
            cur.execute("""
                INSERT INTO dollar_balance_log 
                (change_amount, previous_balance, new_balance, change_type, description, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                correction_amount,
                current_balance,
                correct_balance,
                'correction',
                f'Balance correction: Added ${correction_amount:.2f} to fix negative balance',
                1  # Admin user
            ))

            conn.commit()
            print(f"✅ Balance corrected successfully!")
            print(f"   Old balance: ${current_balance:.2f}")
            print(f"   Correction: +${correction_amount:.2f}")
            print(f"   New balance: ${correct_balance:.2f}")

        else:
            print("❌ Correction cancelled")
            conn.rollback()

    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    fix_dollar_balance()