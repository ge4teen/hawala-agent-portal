from flask import Blueprint, render_template, request, session, redirect, url_for, flash, current_app
from .utils import require_role
from .sms import send_sms
from datetime import datetime
from .models import db, Transaction, User, Branch, Log, Notification, Currency
from sqlalchemy import func, case, or_, and_
from decimal import Decimal

agent_bp = Blueprint("agent", __name__, url_prefix="/agent", template_folder="templates")


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------
@agent_bp.route("/dashboard")
@require_role("agent")
def dashboard():
    uid = session.get("user_id")

    # Stats - Count PENDING transactions (both assigned AND available)
    query = db.session.query(
        # Pending count: assigned to agent
        func.sum(case(
            (and_(Transaction.status == 'pending', Transaction.agent_id == uid), 1),
            else_=0
        )).label('assigned_pending_count'),

        # Available to all pending count
        func.sum(case(
            (and_(
                Transaction.status == 'pending',
                Transaction.available_to_all == True,
                Transaction.agent_id == None
            ), 1),
            else_=0
        )).label('available_pending_count'),

        # Pending volume: assigned to agent
        func.sum(case(
            (and_(Transaction.status == 'pending', Transaction.agent_id == uid), Transaction.amount_local),
            else_=0
        )).label('assigned_pending_volume'),

        # Available to all pending volume
        func.sum(case(
            (and_(
                Transaction.status == 'pending',
                Transaction.available_to_all == True,
                Transaction.agent_id == None
            ), Transaction.amount_local),
            else_=0
        )).label('available_pending_volume'),

        # Completed count & volume (only assigned to agent)
        func.sum(case(
            (and_(Transaction.status == 'completed', Transaction.agent_id == uid), 1),
            else_=0
        )).label('completed_count'),

        func.sum(case(
            (and_(Transaction.status == 'completed', Transaction.agent_id == uid), Transaction.amount_local),
            else_=0
        )).label('completed_volume'),

        # Total count & volume (assigned to agent only)
        func.sum(case(
            (Transaction.agent_id == uid, 1),
            else_=0
        )).label('total_assigned_count'),

        func.sum(case(
            (Transaction.agent_id == uid, Transaction.amount_local),
            else_=0
        )).label('total_assigned_volume')
    ).filter(
        or_(
            Transaction.agent_id == uid,
            and_(Transaction.available_to_all == True, Transaction.agent_id == None)
        )
    )

    row = query.first()

    # Calculate totals including available transactions
    total_pending_count = (row.assigned_pending_count or 0) + (row.available_pending_count or 0)
    total_pending_volume = float(row.assigned_pending_volume or 0.0) + float(row.available_pending_volume or 0.0)
    total_count = (row.total_assigned_count or 0) + (row.available_pending_count or 0)
    total_volume = float(row.total_assigned_volume or 0.0) + float(row.available_pending_volume or 0.0)

    stats = {
        "pending_count": total_pending_count,
        "pending_volume": total_pending_volume,
        "completed_count": row.completed_count or 0,
        "completed_volume": float(row.completed_volume) if row.completed_volume else 0.0,
        "total_count": total_count,
        "total_volume": total_volume,

        # Additional stats for more detail if needed
        "assigned_pending_count": row.assigned_pending_count or 0,
        "available_pending_count": row.available_pending_count or 0,
        "assigned_pending_volume": float(row.assigned_pending_volume) if row.assigned_pending_volume else 0.0,
        "available_pending_volume": float(row.available_pending_volume) if row.available_pending_volume else 0.0,
    }

    return render_template("agent/dashboard.html", stats=stats)


# ---------------------------------------------------------
# Completed Transactions - ADD THIS ROUTE!
# ---------------------------------------------------------
@agent_bp.route("/completed")
@require_role("agent")
def completed_transactions():
    uid = session.get("user_id")
    txs = Transaction.query.filter_by(
        agent_id=uid,
        status='completed'
    ).order_by(Transaction.timestamp.desc()).all()
    return render_template("agent/completed.html", txs=txs)


# ---------------------------------------------------------
# Available Transactions (for all agents) - FIXED VERSION
# ---------------------------------------------------------
@agent_bp.route("/available")
@require_role("agent")
def available_transactions():
    uid = session.get("user_id")

    # DEBUG: Log what we're looking for
    print(f"\n=== AGENT {uid} LOOKING FOR AVAILABLE TRANSACTIONS ===")

    # Get transactions that are:
    # 1. Available to all agents (available_to_all = True)
    # 2. Status is 'pending' (not completed/cancelled)
    # 3. Not assigned to any agent yet (agent_id IS NULL) - CRITICAL FIX!
    # 4. Either never picked or picked by current agent (for fairness)
    results = db.session.query(
        Transaction,
        User.full_name.label('created_by_name')
    ).outerjoin(
        User, Transaction.created_by == User.id
    ).filter(
        Transaction.available_to_all == True,
        Transaction.status == 'pending',
        Transaction.agent_id == None,  # THIS IS THE CRITICAL FIX!
        or_(
            Transaction.picked_by == None,
            Transaction.picked_by == uid
        )
    ).order_by(Transaction.timestamp.desc()).all()

    # DEBUG: Show what we found
    print(f"Found {len(results)} available transactions for agent {uid}")

    # Extract just Transaction objects
    txs = []
    for tx, created_by_name in results:
        print(f"  - {tx.transaction_id}: {tx.sender_name} → {tx.receiver_name}, "
              f"ZAR {tx.amount_local}, agent_id={tx.agent_id}, picked_by={tx.picked_by}")
        # Add created_by_name as a property to the transaction object
        tx.created_by_name = created_by_name
        txs.append(tx)

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

    print(f"\n=== AGENT {uid} TRYING TO PICK TRANSACTION {txid} ===")

    try:
        # Check if transaction exists and is available
        # MUST check that agent_id IS NULL (not assigned to anyone)
        tx = Transaction.query.filter_by(
            transaction_id=txid,
            available_to_all=True,
            status='pending',
            agent_id=None  # MUST NOT BE ASSIGNED TO ANYONE
        ).first()

        if not tx:
            print(f"DEBUG: Transaction {txid} not available (might already be assigned)")
            flash("Transaction not available or already taken by another agent", "warning")
            return redirect(url_for("agent.available_transactions"))

        print(f"DEBUG: Found transaction {txid}, picked_by={tx.picked_by}")

        # Check if already picked by someone else
        if tx.picked_by and tx.picked_by != uid:
            print(f"DEBUG: Transaction {txid} already picked by agent {tx.picked_by}")
            flash("This transaction was already picked by another agent", "warning")
            return redirect(url_for("agent.available_transactions"))

        # Update transaction - assign to this agent
        print(f"DEBUG: Assigning transaction {txid} to agent {uid}")
        tx.agent_id = uid
        tx.picked_by = uid
        tx.picked_at = datetime.utcnow()
        tx.timestamp = datetime.utcnow()

        # Log the action
        log = Log(
            user_id=uid,
            action="picked_transaction",
            details=f"Picked {txid}"
        )
        db.session.add(log)

        db.session.commit()

        print(f"DEBUG: Successfully picked transaction {txid}")
        flash(f"You have successfully picked transaction {txid}", "success")
        return redirect(url_for("agent.pending_transactions"))

    except Exception as e:
        db.session.rollback()
        print(f"ERROR picking transaction {txid}: {str(e)}")
        flash(f"Error picking transaction: {str(e)}", "danger")
        return redirect(url_for("agent.available_transactions"))


# ---------------------------------------------------------
# Create Transaction
# ---------------------------------------------------------
@agent_bp.route("/create", methods=["GET", "POST"])
@require_role("agent")
def create_transaction():
    if request.method == "POST":
        sender = request.form["sender_name"]
        receiver = request.form["receiver_name"]
        amount = float(request.form["amount"])
        currency = request.form["currency_code"]
        agent_id = session.get("user_id")

        txid = f"TX-{int(datetime.utcnow().timestamp())}"

        tx = Transaction(
            transaction_id=txid,
            sender_name=sender,
            receiver_name=receiver,
            amount_local=amount,
            currency_code=currency,
            status='pending',
            agent_id=agent_id,
            timestamp=datetime.utcnow()
        )

        db.session.add(tx)
        db.session.commit()

        flash("Transaction created!", "success")
        return redirect(url_for("agent.pending_transactions"))

    currencies = Currency.query.all()
    return render_template("agent/create.html", currencies=currencies)


# ---------------------------------------------------------
# Verify Transaction
# ---------------------------------------------------------
@agent_bp.route("/verify/<txid>")
@require_role("agent")
def verify_transaction(txid):  # Renamed to avoid conflict
    tx = Transaction.query.filter_by(transaction_id=txid).first()

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
    uid = session.get("user_id")

    # Get agent name
    agent = User.query.get(uid)
    agent_name = agent.full_name if agent else f"Agent {uid}"

    try:
        # Update transaction with who completed it
        tx = Transaction.query.filter_by(transaction_id=txid).first()
        if not tx:
            flash("Transaction not found", "danger")
            return redirect(url_for("agent.dashboard"))

        tx.status = 'completed'
        tx.completed_by = uid
        tx.completed_at = datetime.utcnow()
        tx.timestamp = datetime.utcnow()

        # Log the completion
        log = Log(
            user_id=uid,
            action="completed_tx",
            details=f"{txid} completed by {agent_name}"
        )
        db.session.add(log)

        db.session.commit()

        # Send SMS to sender if phone present
        if tx.sender_phone:
            msg = f"ISA Southern Solutions: Your transfer {txid} has been completed by {agent_name}. Amount: ZAR {tx.amount_local}."
            resp = send_sms(tx.sender_phone, msg)

            # Log SMS
            sms_log = Log(
                user_id=uid,
                action="sms_sent",
                details=f"To: {tx.sender_phone} - Completed notification"
            )
            db.session.add(sms_log)
            db.session.commit()

        flash(f"Transaction {txid} marked as completed by {agent_name}", "success")

        # Notify admin
        notify_admin_transaction_completed(txid, agent_name, uid)

    except Exception as e:
        db.session.rollback()
        flash(f"Error completing transaction: {str(e)}", "danger")

    return redirect(url_for("agent.available_transactions"))


def notify_admin_transaction_completed(txid, agent_name, agent_id):
    """Notify admin when a transaction is completed"""
    try:
        # Get transaction details
        tx = Transaction.query.filter_by(transaction_id=txid).first()
        if not tx:
            return

        # Notify the admin who created it
        notification = Notification(
            user_id=tx.created_by,  # Notify the admin who created it
            type='transaction_completed',
            title='Transaction Completed',
            message=f'Transaction {txid} (ZAR {tx.amount_local:.2f}) was completed by {agent_name}',
            link='/admin/transactions',
            is_read=False,
            created_at=datetime.utcnow()
        )

        db.session.add(notification)
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to notify admin: {str(e)}")


# ---------------------------------------------------------
# Debug Available Transactions (for testing)
# ---------------------------------------------------------
@agent_bp.route("/debug-available")
@require_role("agent")
def debug_available():
    """Debug route to see what's in the database"""
    uid = session.get("user_id")

    print(f"\n=== DEBUG AVAILABLE TRANSACTIONS FOR AGENT {uid} ===")

    # Show ALL transactions with available_to_all = True
    all_available = Transaction.query.filter_by(available_to_all=True).order_by(Transaction.timestamp.desc()).all()

    debug_info = {
        "agent_id": uid,
        "total_available_in_db": len(all_available),
        "transactions": []
    }

    for tx in all_available:
        # Check if this transaction should be available to current agent
        is_available = (
                tx.status == 'pending' and
                tx.agent_id is None and
                (tx.picked_by is None or tx.picked_by == uid)
        )

        tx_info = {
            "transaction_id": tx.transaction_id,
            "sender": tx.sender_name,
            "receiver": tx.receiver_name,
            "amount": tx.amount_local,
            "status": tx.status,
            "available_to_all": tx.available_to_all,
            "agent_id": tx.agent_id,
            "picked_by": tx.picked_by,
            "timestamp": tx.timestamp,
            "is_available_for_me": is_available
        }
        debug_info["transactions"].append(tx_info)

        print(f"  TX {tx.transaction_id}: status={tx.status}, "
              f"available={tx.available_to_all}, agent={tx.agent_id}, "
              f"picked={tx.picked_by}, available_for_me={is_available}")

    print(f"=== END DEBUG ===\n")

    # What the fixed query returns
    query_result = db.session.query(
        Transaction,
        User.full_name.label('created_by_name')
    ).outerjoin(
        User, Transaction.created_by == User.id
    ).filter(
        Transaction.available_to_all == True,
        Transaction.status == 'pending',
        Transaction.agent_id == None,
        or_(
            Transaction.picked_by == None,
            Transaction.picked_by == uid
        )
    ).order_by(Transaction.timestamp.desc()).all()

    debug_info["query_result_count"] = len(query_result)
    debug_info["query_transaction_ids"] = [tx.transaction_id for tx, _ in query_result]

    return render_template("agent/debug_available.html", debug_info=debug_info)


# ---------------------------------------------------------
# Pending Transactions (ASSIGNED TO AGENT)
# ---------------------------------------------------------
@agent_bp.route("/pending")
@require_role("agent")
def pending_transactions():
    uid = session.get("user_id")

    # Get transactions assigned to this agent that are pending
    results = db.session.query(
        Transaction,
        User.full_name.label('created_by_name')
    ).outerjoin(
        User, Transaction.created_by == User.id
    ).filter(
        Transaction.agent_id == uid,
        Transaction.status == 'pending'
    ).order_by(Transaction.timestamp.desc()).all()

    # Extract Transaction objects
    txs = []
    for tx, created_by_name in results:
        tx.created_by_name = created_by_name
        txs.append(tx)

    return render_template("agent/pending.html", txs=txs)
@agent_bp.route("/view/<txid>")
@require_role("agent")
def view_transaction(txid):
    # Get transaction with additional info
    result = db.session.query(
        Transaction,
        User.full_name.label('agent_name'),
        User.full_name.label('created_by_name'),
        User.full_name.label('completed_by_name'),
        Branch.name.label('branch_name')
    ).outerjoin(
        User, Transaction.agent_id == User.id
    ).outerjoin(
        User, Transaction.created_by == User.id
    ).outerjoin(
        User, Transaction.completed_by == User.id
    ).outerjoin(
        Branch, Transaction.branch_id == Branch.id
    ).filter(
        Transaction.transaction_id == txid
    ).first()

    if not result:
        flash("Transaction not found", "warning")
        return redirect(url_for("agent.dashboard"))

    # Extract transaction and add properties
    tx, agent_name, created_by_name, completed_by_name, branch_name = result
    tx.agent_name = agent_name
    tx.created_by_name = created_by_name
    tx.completed_by_name = completed_by_name
    tx.branch_name = branch_name

    return render_template("agent/view_transaction.html", tx=tx)