from flask import Blueprint, render_template, request, session, redirect, url_for, flash, current_app
from .utils import get_db, require_role
from .sms import send_sms
from datetime import datetime

agent_bp = Blueprint("agent", __name__, url_prefix="/agent", template_folder="templates")


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------
@agent_bp.route("/dashboard")
@require_role("agent")
def dashboard():
    uid = session.get("user_id")
    db = get_db()
    cur = db.cursor()

    # Stats - Count PENDING transactions (both assigned AND available)
    cur.execute("""
        SELECT 
            -- Pending count: assigned to agent + available to all
            COUNT(CASE WHEN status='pending' AND agent_id=? THEN 1 END) as assigned_pending_count,
            COUNT(CASE WHEN status='pending' AND available_to_all=1 AND agent_id IS NULL THEN 1 END) as available_pending_count,

            -- Pending volume: assigned to agent + available to all  
            IFNULL(SUM(CASE WHEN status='pending' AND agent_id=? THEN amount_local ELSE 0 END), 0) as assigned_pending_volume,
            IFNULL(SUM(CASE WHEN status='pending' AND available_to_all=1 AND agent_id IS NULL THEN amount_local ELSE 0 END), 0) as available_pending_volume,

            -- Completed count & volume (only assigned to agent)
            COUNT(CASE WHEN status='completed' AND agent_id=? THEN 1 END) as completed_count,
            IFNULL(SUM(CASE WHEN status='completed' AND agent_id=? THEN amount_local ELSE 0 END), 0) as completed_volume,

            -- Total count & volume (assigned to agent only)
            COUNT(CASE WHEN agent_id=? THEN 1 END) as total_assigned_count,
            IFNULL(SUM(CASE WHEN agent_id=? THEN amount_local ELSE 0 END), 0) as total_assigned_volume
        FROM transactions 
        WHERE (agent_id=? OR (available_to_all=1 AND agent_id IS NULL))
    """, (uid, uid, uid, uid, uid, uid, uid))

    row = cur.fetchone()

    # Calculate totals including available transactions
    total_pending_count = (row['assigned_pending_count'] or 0) + (row['available_pending_count'] or 0)
    total_pending_volume = (row['assigned_pending_volume'] or 0.0) + (row['available_pending_volume'] or 0.0)
    total_count = (row['total_assigned_count'] or 0) + (row['available_pending_count'] or 0)
    total_volume = (row['total_assigned_volume'] or 0.0) + (row['available_pending_volume'] or 0.0)

    stats = {
        "pending_count": total_pending_count,
        "pending_volume": total_pending_volume,
        "completed_count": row['completed_count'] or 0,
        "completed_volume": float(row['completed_volume']) if row['completed_volume'] else 0.0,
        "total_count": total_count,
        "total_volume": total_volume,

        # Additional stats for more detail if needed
        "assigned_pending_count": row['assigned_pending_count'] or 0,
        "available_pending_count": row['available_pending_count'] or 0,
        "assigned_pending_volume": float(row['assigned_pending_volume']) if row['assigned_pending_volume'] else 0.0,
        "available_pending_volume": float(row['available_pending_volume']) if row['available_pending_volume'] else 0.0,
    }

    return render_template("agent/dashboard.html", stats=stats)

# ---------------------------------------------------------
# Completed Transactions - ADD THIS ROUTE!
# ---------------------------------------------------------
@agent_bp.route("/completed")
@require_role("agent")
def completed_transactions():
    uid = session.get("user_id")
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM transactions WHERE agent_id=? AND status='completed' ORDER BY timestamp DESC", (uid,))
    txs = cur.fetchall()
    return render_template("agent/completed.html", txs=txs)

# ---------------------------------------------------------
# Available Transactions (for all agents) - FIXED VERSION
# ---------------------------------------------------------
@agent_bp.route("/available")
@require_role("agent")
def available_transactions():
    uid = session.get("user_id")
    db = get_db()
    cur = db.cursor()

    # DEBUG: Log what we're looking for
    print(f"\n=== AGENT {uid} LOOKING FOR AVAILABLE TRANSACTIONS ===")

    # Get transactions that are:
    # 1. Available to all agents (available_to_all = 1)
    # 2. Status is 'pending' (not completed/cancelled)
    # 3. Not assigned to any agent yet (agent_id IS NULL) - CRITICAL FIX!
    # 4. Either never picked or picked by current agent (for fairness)
    cur.execute("""
        SELECT t.*, u.full_name as created_by_name 
        FROM transactions t 
        LEFT JOIN users u ON t.created_by = u.id
        WHERE t.available_to_all = 1 
        AND t.status = 'pending'
        AND t.agent_id IS NULL  -- THIS IS THE CRITICAL FIX!
        AND (t.picked_by IS NULL OR t.picked_by = ?)
        ORDER BY t.timestamp DESC
    """, (uid,))

    txs = cur.fetchall()

    # DEBUG: Show what we found
    print(f"Found {len(txs)} available transactions for agent {uid}")
    for tx in txs:
        print(f"  - {tx['transaction_id']}: {tx['sender_name']} → {tx['receiver_name']}, "
              f"ZAR {tx['amount_local']}, agent_id={tx['agent_id']}, picked_by={tx['picked_by']}")
    print("=== END DEBUG ===\n")

    return render_template("agent/available.html", txs=txs)
# ---------------------------------------------------------
# Pick Available Transaction - FIXED VERSION
# ---------------------------------------------------------
@agent_bp.route("/pick/<txid>", methods=["POST"])
@require_role("agent")
def pick_transaction(txid):
    """Agent picks an available transaction"""
    uid = session.get("user_id")
    db = get_db()
    cur = db.cursor()

    print(f"\n=== AGENT {uid} TRYING TO PICK TRANSACTION {txid} ===")

    try:
        # Check if transaction exists and is available
        # MUST check that agent_id IS NULL (not assigned to anyone)
        cur.execute("""
            SELECT * FROM transactions 
            WHERE transaction_id = ? 
            AND available_to_all = 1 
            AND status = 'pending'
            AND agent_id IS NULL  -- MUST NOT BE ASSIGNED TO ANYONE
        """, (txid,))

        tx = cur.fetchone()

        if not tx:
            print(f"DEBUG: Transaction {txid} not available (might already be assigned)")
            flash("Transaction not available or already taken by another agent", "warning")
            return redirect(url_for("agent.available_transactions"))

        print(f"DEBUG: Found transaction {txid}, picked_by={tx['picked_by']}")

        # Check if already picked by someone else
        if tx['picked_by'] and tx['picked_by'] != uid:
            print(f"DEBUG: Transaction {txid} already picked by agent {tx['picked_by']}")
            flash("This transaction was already picked by another agent", "warning")
            return redirect(url_for("agent.available_transactions"))

        # Update transaction - assign to this agent
        print(f"DEBUG: Assigning transaction {txid} to agent {uid}")
        cur.execute("""
            UPDATE transactions 
            SET agent_id = ?, 
                picked_by = ?, 
                picked_at = datetime('now'),
                timestamp = datetime('now')
            WHERE transaction_id = ?
        """, (uid, uid, txid))

        # Log the action
        cur.execute("""
            INSERT INTO logs (user_id, action, details) 
            VALUES (?, ?, ?)
        """, (uid, "picked_transaction", f"Picked {txid}"))

        db.commit()

        print(f"DEBUG: Successfully picked transaction {txid}")
        flash(f"You have successfully picked transaction {txid}", "success")
        return redirect(url_for("agent.pending_transactions"))

    except Exception as e:
        db.rollback()
        print(f"ERROR picking transaction {txid}: {str(e)}")
        flash(f"Error picking transaction: {str(e)}", "danger")
        return redirect(url_for("agent.available_transactions"))
# ---------------------------------------------------------
# Create Transaction
# ---------------------------------------------------------
@agent_bp.route("/create", methods=["GET", "POST"])
@require_role("agent")
def create_transaction():
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        sender = request.form["sender_name"]
        receiver = request.form["receiver_name"]
        amount = float(request.form["amount"])
        currency = request.form["currency_code"]
        agent_id = session.get("user_id")

        txid = f"TX-{int(datetime.utcnow().timestamp())}"

        cur.execute("""
            INSERT INTO transactions (
                transaction_id, sender_name, receiver_name,
                amount_local, currency_code, status,
                agent_id, timestamp
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, datetime('now'))
        """, (txid, sender, receiver, amount, currency, agent_id))

        db.commit()
        flash("Transaction created!", "success")
        return redirect(url_for("agent.pending_transactions"))

    cur.execute("SELECT code FROM currencies")
    currencies = cur.fetchall()

    return render_template("agent/create.html", currencies=currencies)


# ---------------------------------------------------------
# Verify Transaction
# ---------------------------------------------------------
@agent_bp.route("/verify/<txid>")
@require_role("agent")
def verify_transaction(txid):  # Renamed to avoid conflict
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM transactions WHERE transaction_id=?", (txid,))
    tx = cur.fetchone()

    if not tx:
        flash("Transaction not found", "warning")
        return redirect(url_for("agent.dashboard"))

    return render_template("agent/verify.html", tx=tx)


# ---------------------------------------------------------
# Complete Transaction
# ---------------------------------------------------------
@agent_bp.route("/complete/<txid>", methods=["POST"])
@require_role("agent")
def complete_transaction(txid):  # Renamed to avoid conflict
    db = get_db()
    cur = db.cursor()
    uid = session.get("user_id")

    # Get agent name
    cur.execute("SELECT full_name FROM users WHERE id=?", (uid,))
    agent = cur.fetchone()
    agent_name = agent['full_name'] if agent else f"Agent {uid}"

    try:
        # Update transaction with who completed it
        cur.execute("""
            UPDATE transactions 
            SET status='completed', 
                completed_by=?, 
                completed_at=datetime('now'),
                timestamp=datetime('now') 
            WHERE transaction_id=?
        """, (uid, txid))

        # Log the completion
        cur.execute("""
            INSERT INTO logs (user_id, action, details) 
            VALUES (?, ?, ?)
        """, (uid, "completed_tx", f"{txid} completed by {agent_name}"))

        db.commit()

        # Send SMS to sender if phone present
        cur.execute("""
            SELECT sender_phone, sender_name, amount_local 
            FROM transactions 
            WHERE transaction_id=?
        """, (txid,))

        tx = cur.fetchone()
        if tx and tx["sender_phone"]:
            msg = f"ISA Southern Solutions: Your transfer {txid} has been completed by {agent_name}. Amount: ZAR {tx['amount_local']}."
            resp = send_sms(tx["sender_phone"], msg)

            # Log SMS
            cur.execute("""
                INSERT INTO logs (user_id, action, details) 
                VALUES (?, ?, ?)
            """, (uid, "sms_sent", f"To: {tx['sender_phone']} - Completed notification"))
            db.commit()

        flash(f"Transaction {txid} marked as completed by {agent_name}", "success")

        # Notify admin
        notify_admin_transaction_completed(txid, agent_name, uid)

    except Exception as e:
        db.rollback()
        flash(f"Error completing transaction: {str(e)}", "danger")

    return redirect(url_for("agent.available_transactions"))


def notify_admin_transaction_completed(txid, agent_name, agent_id):
    """Notify admin when a transaction is completed"""
    db = get_db()
    cur = db.cursor()

    try:
        # Get transaction details
        cur.execute("""
            SELECT created_by, amount_local, sender_name, receiver_name 
            FROM transactions 
            WHERE transaction_id=?
        """, (txid,))

        tx = cur.fetchone()
        if not tx:
            return

        # Notify the admin who created it
        cur.execute("""
            INSERT INTO notifications 
            (user_id, type, title, message, link, is_read, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            tx['created_by'],  # Notify the admin who created it
            'transaction_completed',
            'Transaction Completed',
            f'Transaction {txid} (ZAR {tx["amount_local"]:.2f}) was completed by {agent_name}',
            f'/admin/transactions',

        ))

        db.commit()

    except Exception as e:
        db.rollback()
        current_app.logger.error(f"Failed to notify admin: {str(e)}")


# ---------------------------------------------------------
# Debug Available Transactions (for testing)
# ---------------------------------------------------------
@agent_bp.route("/debug-available")
@require_role("agent")
def debug_available():
    """Debug route to see what's in the database"""
    uid = session.get("user_id")
    db = get_db()
    cur = db.cursor()

    print(f"\n=== DEBUG AVAILABLE TRANSACTIONS FOR AGENT {uid} ===")

    # Show ALL transactions with available_to_all = 1
    cur.execute("""
        SELECT 
            transaction_id,
            sender_name,
            receiver_name,
            amount_local,
            status,
            available_to_all,
            agent_id,
            picked_by,
            timestamp
        FROM transactions 
        WHERE available_to_all = 1
        ORDER BY timestamp DESC
    """)

    all_available = cur.fetchall()

    debug_info = {
        "agent_id": uid,
        "total_available_in_db": len(all_available),
        "transactions": []
    }

    for tx in all_available:
        # Check if this transaction should be available to current agent
        is_available = (
                tx['status'] == 'pending' and
                tx['agent_id'] is None and
                (tx['picked_by'] is None or tx['picked_by'] == uid)
        )

        tx_info = {
            "transaction_id": tx['transaction_id'],
            "sender": tx['sender_name'],
            "receiver": tx['receiver_name'],
            "amount": tx['amount_local'],
            "status": tx['status'],
            "available_to_all": tx['available_to_all'],
            "agent_id": tx['agent_id'],
            "picked_by": tx['picked_by'],
            "timestamp": tx['timestamp'],
            "is_available_for_me": is_available
        }
        debug_info["transactions"].append(tx_info)

        print(f"  TX {tx['transaction_id']}: status={tx['status']}, "
              f"available={tx['available_to_all']}, agent={tx['agent_id']}, "
              f"picked={tx['picked_by']}, available_for_me={is_available}")

    print(f"=== END DEBUG ===\n")

    # What the fixed query returns
    cur.execute("""
        SELECT t.*, u.full_name as created_by_name 
        FROM transactions t 
        LEFT JOIN users u ON t.created_by = u.id
        WHERE t.available_to_all = 1 
        AND t.status = 'pending'
        AND t.agent_id IS NULL
        AND (t.picked_by IS NULL OR t.picked_by = ?)
        ORDER BY t.timestamp DESC
    """, (uid,))

    query_result = cur.fetchall()
    debug_info["query_result_count"] = len(query_result)
    debug_info["query_transaction_ids"] = [tx['transaction_id'] for tx in query_result]

    return render_template("agent/debug_available.html", debug_info=debug_info)


# ---------------------------------------------------------
# Pending Transactions (ASSIGNED TO AGENT)
# ---------------------------------------------------------
@agent_bp.route("/pending")
@require_role("agent")
def pending_transactions():
    uid = session.get("user_id")
    db = get_db()
    cur = db.cursor()

    # Get transactions assigned to this agent that are pending
    cur.execute("""
        SELECT t.*, u.full_name as created_by_name 
        FROM transactions t 
        LEFT JOIN users u ON t.created_by = u.id
        WHERE t.agent_id = ? 
        AND t.status = 'pending'
        ORDER BY t.timestamp DESC
    """, (uid,))

    txs = cur.fetchall()
    return render_template("agent/pending.html", txs=txs)


@agent_bp.route("/view/<txid>")
@require_role("agent")
def view_transaction(txid):
    db = get_db()
    cur = db.cursor()

    # Get transaction with additional info
    cur.execute("""
        SELECT t.*, 
               u1.full_name as agent_name,
               u2.full_name as created_by_name,
               u3.full_name as completed_by_name,
               b.name as branch_name
        FROM transactions t
        LEFT JOIN users u1 ON t.agent_id = u1.id
        LEFT JOIN users u2 ON t.created_by = u2.id
        LEFT JOIN users u3 ON t.completed_by = u3.id
        LEFT JOIN branches b ON t.branch_id = b.id
        WHERE t.transaction_id = ?
    """, (txid,))

    tx = cur.fetchone()

    if not tx:
        flash("Transaction not found", "warning")
        return redirect(url_for("agent.dashboard"))

    return render_template("agent/view_transaction.html", tx=tx)