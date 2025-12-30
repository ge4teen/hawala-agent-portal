import sqlite3

from flask import Blueprint, render_template, session, redirect, request, url_for, flash, current_app, jsonify

from .helpers import generate_unique_txid
from .sms import send_sms
from .utils import get_db, require_role, update_rate_if_needed, get_latest_rate, set_setting, get_setting
from datetime import datetime,timedelta

admin_bp = Blueprint("admin", __name__, template_folder="templates")

def build_sms_template(
    txid,
    agent,
    sender_name,
    sender_phone,
    receiver_name,
    receiver_phone,
    amount,
    status
):
    return (
        f"ISA Southern Solutions: Transaction ID {txid} assigned to Agent {agent}. "
        f"Sender: {sender_name}, {sender_phone} | Receiver: {receiver_name}, {receiver_phone}. "
        f"Amount: ZAR {amount}. Status: {status}."
    )


@admin_bp.route("/dashboard")
@require_role("admin")
def dashboard():
    db = get_db(); cur = db.cursor()
    # stats
    cur.execute("SELECT COUNT(*) as cnt FROM transactions"); total_transactions = cur.fetchone()["cnt"] or 0
    cur.execute("SELECT COUNT(*) as cnt FROM users WHERE role='agent'"); total_agents = cur.fetchone()["cnt"] or 0
    cur.execute("SELECT IFNULL(SUM(amount_local),0) as s FROM transactions WHERE date(timestamp)=date('now')"); today_volume = cur.fetchone()["s"] or 0.0
    # charts last 7 days
    cur.execute("SELECT date(timestamp) as d, IFNULL(SUM(amount_local),0) as s FROM transactions GROUP BY date(timestamp) ORDER BY d DESC LIMIT 7")
    rows = cur.fetchall(); labels = [r["d"] for r in reversed(rows)]; data = [r["s"] for r in reversed(rows)]
    # rates: update if needed (auto)
    latest = update_rate_if_needed(force=False)
    usd_zar = latest["rate"] if latest else None
    stats = {"total_transactions": total_transactions, "total_agents": total_agents, "today_volume": today_volume}
    charts = {"transfers": {"labels": labels, "data": data}}
    return render_template("admin/dashboard.html", stats=stats, charts=charts, usd_zar=usd_zar)

# transactions list & management
@admin_bp.route("/transactions")
@require_role("admin")
def transactions():
    db = get_db()
    cur = db.cursor()

    # Get search parameters
    txid_suffix = request.args.get('txid', '').upper().strip()
    status = request.args.get('status', '')

    # Build base query with joins
    query = """
        SELECT 
            t.*, 
            u1.full_name as agent_name,
            u2.full_name as completed_by_name,
            u3.full_name as verified_by_name
        FROM transactions t 
        LEFT JOIN users u1 ON t.agent_id = u1.id
        LEFT JOIN users u2 ON t.completed_by = u2.id
        LEFT JOIN users u3 ON t.verified_by = u3.id
        WHERE 1=1
    """
    params = []

    # Add TXID filter (exact match for suffix)
    if txid_suffix:
        query += " AND UPPER(t.transaction_id) = ?"
        params.append('ISA-' + txid_suffix)  # Add prefix here

    # Add status filter
    if status:
        query += " AND t.status = ?"
        params.append(status)

    # Always order by most recent first
    query += " ORDER BY t.timestamp DESC"

    # Execute query
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)

    txs = cur.fetchall()
    return render_template("admin/transactions.html", txs=txs)

@admin_bp.route("/transactions/create", methods=["GET", "POST"])
@require_role("admin")
def create_transaction():
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        # Check if this is a confirmed transaction (after quote)
        if request.form.get("confirmed") == "true" or request.form.get("action") == "create":
            # Get form data
            sender_name = request.form.get("sender_name") or ""
            sender_phone = request.form.get("sender_phone") or ""
            receiver_name = request.form.get("receiver_name") or ""
            receiver_phone = request.form.get("receiver_phone") or ""
            payment_method = request.form.get("payment_method") or "cash"
            notes = request.form.get("notes") or ""

            # Amount parsing
            raw_amount = request.form.get("amount_local") or request.form.get("amount") or "0"
            try:
                amount_local = float(raw_amount)
            except ValueError:
                flash("Invalid amount", "warning")
                return redirect(url_for("admin.create_transaction"))

            currency_code = request.form.get("currency_code") or "ZAR"

            # Agent selection
            agent_val = request.form.get("agent_id")
            agent_id = int(agent_val) if agent_val not in (None, "", "None") else None

            # Branch selection
            branch_val = request.form.get("branch_id")
            branch_id = int(branch_val) if branch_val not in (None, "", "None") else None

            # Available to all agents
            available_to_all_val = request.form.get("available_to_all")
            available_to_all = 1 if available_to_all_val == "1" else 0

            # Agent selection - FIXED VERSION
            agent_val = request.form.get("agent_id")
            available_to_all_val = request.form.get("available_to_all")
            available_to_all = 1 if available_to_all_val == "1" else 0

            # ✅ CRITICAL FIX: If available_to_all is checked, agent_id MUST be NULL
            if available_to_all == 1:
                agent_id = None  # Force NULL for available_to_all transactions
                print(f"DEBUG: available_to_all=1, so forcing agent_id to NULL")
            else:
                agent_id = int(agent_val) if agent_val not in (None, "", "None") else None
                print(f"DEBUG: available_to_all=0, using agent_id: {agent_id}")

            # Branch selection
            branch_val = request.form.get("branch_id")
            branch_id = int(branch_val) if branch_val not in (None, "", "None") else None

            # Status
            status = request.form.get("status") or "pending"
            # ✅ ADD THIS DEBUG LOGGING:
            print(f"\n=== DEBUG: CREATING TRANSACTION ===")
            print(f"Form data - available_to_all: '{available_to_all_val}' (converted to: {available_to_all})")
            print(f"Form data - agent_id from form: '{agent_val}'")
            print(f"Current agent_id before logic: {agent_id}")

            # Status
            status = request.form.get("status") or "pending"

            # Get exchange rate
            cur.execute(
                "SELECT rate FROM exchange_rates WHERE from_currency=? AND to_currency=? ORDER BY updated_at DESC LIMIT 1",
                (currency_code, "ZAR")
            )
            row = cur.fetchone()
            rate = float(row["rate"]) if row and row["rate"] else 1.0

            if rate == 0:
                rate = 1.0
            amount_foreign = round(amount_local / rate, 6)

            # Generate unique transaction ID
            txid = generate_unique_txid()

            try:
                # First, check if we have enough balance (only if subtracting)
                cur.execute("SELECT current_balance FROM dollar_balance WHERE id = 1")
                balance_row = cur.fetchone()
                current_balance = float(balance_row['current_balance']) if balance_row else 0.0

                # Optional: Add balance check warning (don't block transaction, just warn)
                if amount_foreign > current_balance:
                    print(
                        f"WARNING: Transaction amount (${amount_foreign:.2f}) exceeds current balance (${current_balance:.2f})")

                # ✅ ADD MORE DEBUG INFO:
                print(f"DEBUG: Transaction details to insert:")
                print(f"  - transaction_id: {txid}")
                print(f"  - available_to_all: {available_to_all}")
                print(f"  - agent_id: {agent_id}")
                print(f"  - status: {status}")
                print(f"  - amount_local: ZAR {amount_local:.2f}")
                print(f"=== END DEBUG ===\n")

                # Insert transaction
                cur.execute("""
                    INSERT INTO transactions
                    (transaction_id, sender_name, sender_phone, receiver_name, receiver_phone, 
                     amount_local, amount_foreign, currency_code, status, created_by, 
                     agent_id, branch_id, token, available_to_all, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    txid, sender_name, sender_phone, receiver_name, receiver_phone,
                    amount_local, amount_foreign, currency_code, status,
                    session.get("user_id"), agent_id, branch_id, None,
                    available_to_all, payment_method, notes
                ))
                db.commit()
                transaction_created = True

                # ✅ NEW: Update dollar balance after successful transaction creation
                try:
                    # Get current balance again (in case it changed)
                    cur.execute("SELECT current_balance FROM dollar_balance WHERE id = 1")
                    balance_row = cur.fetchone()
                    current_balance = float(balance_row['current_balance']) if balance_row else 0.0

                    # Calculate new balance (subtract for outgoing transactions)
                    new_balance = current_balance - amount_foreign

                    # Update main balance
                    cur.execute("""
                        UPDATE dollar_balance 
                        SET current_balance = ?, last_updated = datetime('now')
                        WHERE id = 1
                    """, (new_balance,))

                    # Log the change
                    cur.execute("""
                        INSERT INTO dollar_balance_log 
                        (transaction_id, change_amount, previous_balance, new_balance, change_type, description, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        txid,
                        -amount_foreign,  # Negative for outgoing
                        current_balance,
                        new_balance,
                        'transaction_outgoing',
                        f"Transaction: {sender_name[:20]} → {receiver_name[:20]}",
                        session.get("user_id")
                    ))

                    db.commit()
                    print(
                        f"DEBUG: Dollar balance updated: ${current_balance:.2f} → ${new_balance:.2f} (-${amount_foreign:.2f})")

                except Exception as balance_error:
                    # Don't rollback the transaction, just log the balance update error
                    print(f"WARNING: Could not update dollar balance: {balance_error}")
                    cur.execute(
                        "INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)",
                        (session.get("user_id"), "balance_update_error",
                         f"Failed to update balance for {txid}: {str(balance_error)[:200]}")
                    )
                    db.commit()
                    new_balance = current_balance  # Keep old balance if update failed

            except sqlite3.IntegrityError as e:
                db.rollback()
                flash(f"Could not create transaction: {str(e)}", "danger")
                return redirect(url_for("admin.transactions"))
            except Exception as e:
                db.rollback()
                flash(f"Error creating transaction: {str(e)}", "danger")
                return redirect(url_for("admin.transactions"))

            # SMS notification if receiver phone provided
            if receiver_phone and transaction_created:
                # Get agent name for SMS
                if agent_id:
                    cur.execute("SELECT full_name FROM users WHERE id=?", (agent_id,))
                    arow = cur.fetchone()
                    agent_display = arow["full_name"] if arow else f"ID:{agent_id}"
                else:
                    agent_display = "Unassigned"

                # Build and send SMS
                msg = build_sms_template(
                    txid=txid,
                    agent=agent_display,
                    sender_name=sender_name,
                    sender_phone=sender_phone,
                    receiver_name=receiver_name,
                    receiver_phone=receiver_phone,
                    amount=amount_local,
                    status=status.capitalize()
                )

                try:
                    resp = send_sms(receiver_phone, msg)

                    # Log SMS attempt
                    cur.execute(
                        "INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)",
                        (session.get("user_id"), "sms_sent", f"To: {receiver_phone} - {str(resp)[:200]}")
                    )
                    db.commit()
                except Exception as e:
                    # Log error but don't fail transaction
                    cur.execute(
                        "INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)",
                        (session.get("user_id"), "sms_error", f"Failed to send SMS: {str(e)[:200]}")
                    )
                    db.commit()

            # GET request - show the form
            cur.execute("SELECT id, full_name FROM users WHERE role='agent'")
            agents = cur.fetchall()
            cur.execute("SELECT id, name FROM branches")
            branches = cur.fetchall()
            cur.execute("SELECT code FROM currencies")
            currencies = cur.fetchall()

            # ✅ ADD THIS: Get the latest exchange rate
            cur.execute("""
                SELECT rate, updated_at 
                FROM exchange_rates 
                WHERE from_currency='USD' AND to_currency='ZAR' 
                ORDER BY updated_at DESC LIMIT 1
            """)
            latest_rate = cur.fetchone()

            # Get system fee settings
            fee_percent = current_app.config.get("FEE_PERCENT", 0.01)  # 1%
            fee_flat = current_app.config.get("FEE_FLAT", 10.0)  # 10 ZAR flat fee

            # Calculate quote for success message
            pct = float(current_app.config.get("FEE_PERCENT", 0.01))
            flat = float(current_app.config.get("FEE_FLAT", 10.0))
            fee_percent = round(amount_local * pct, 2)
            subtotal = round(amount_local + fee_percent + flat, 2)

            # Get final balance for display
            cur.execute("SELECT current_balance FROM dollar_balance WHERE id = 1")
            final_balance_row = cur.fetchone()
            final_balance = float(final_balance_row['current_balance']) if final_balance_row else 0.0

            flash(f"""
            ✅ Transaction created successfully!

            📋 Transaction Details:
            • Transaction ID: {txid}
            • Sender: {sender_name}
            • Receiver: {receiver_name}
            • Amount: ZAR {amount_local:.2f} (USD {amount_foreign:.2f})
            • Payment Method: {payment_method}
            • Status: {status}
            • Agent: {agent_display if 'agent_display' in locals() else 'Unassigned'}

            💰 Quote Summary:
            • Base Amount: ZAR {amount_local:.2f}
            • Fee ({pct * 100}%): ZAR {fee_percent:.2f}
            • Flat Fee: ZAR {flat:.2f}
            • Total: ZAR {subtotal:.2f}
            • Exchange Rate: {rate:.4f}
            • Foreign Amount: {currency_code} {amount_foreign:.2f}

            💵 Dollar Balance Impact:
            • Transaction Amount: ${amount_foreign:.2f}
            • New System Balance: ${final_balance:.2f}
            """, "success")

            return redirect(url_for("admin.transactions"))

        # If not confirmed, show form again
        return redirect(url_for("admin.create_transaction"))

    # GET request - show the form
    cur.execute("SELECT id, full_name FROM users WHERE role='agent'")
    agents = cur.fetchall()
    cur.execute("SELECT id, name FROM branches")
    branches = cur.fetchall()
    cur.execute("SELECT code FROM currencies")
    currencies = cur.fetchall()

    return render_template("admin/create_transaction.html",
                           agents=agents,
                           branches=branches,
                           currencies=currencies)


@admin_bp.route("/transactions/<txid>/edit", methods=["GET", "POST"])
@require_role("admin")
def edit_transaction(txid):
    db = get_db();
    cur = db.cursor()
    cur.execute("SELECT * FROM transactions WHERE transaction_id=?", (txid,))
    tx = cur.fetchone()
    if not tx:
        flash("Transaction not found", "warning");
        return redirect(url_for("admin.transactions"))

    # GET request - show form
    if request.method == "GET":
        # Get data for dropdowns (same as create)
        cur.execute("SELECT id, full_name FROM users WHERE role='agent'")
        agents = cur.fetchall()
        cur.execute("SELECT id, name FROM branches")
        branches = cur.fetchall()
        cur.execute("SELECT code FROM currencies")
        currencies = cur.fetchall()

        return render_template("admin/edit_transaction.html",
                               tx=tx, agents=agents, branches=branches, currencies=currencies)

    # POST request - update transaction
    if request.method == "POST":
        # Get all form fields (matching create form)
        sender_name = request.form.get("sender_name") or tx["sender_name"]
        sender_phone = request.form.get("sender_phone") or tx["sender_phone"]
        receiver_name = request.form.get("receiver_name") or tx["receiver_name"]
        receiver_phone = request.form.get("receiver_phone") or tx["receiver_phone"]
        payment_method = request.form.get("payment_method") or tx["payment_method"] or "cash"
        notes = request.form.get("notes") or tx["notes"] or ""

        # Amount
        raw_amount = request.form.get("amount_local")
        if raw_amount is None or raw_amount == "":
            amount_local = tx["amount_local"]
        else:
            try:
                amount_local = float(raw_amount)
            except ValueError:
                flash("Invalid amount", "warning");
                return redirect(url_for("admin.edit_transaction", txid=txid))

        currency = request.form.get("currency_code") or tx["currency_code"] or "ZAR"

        # Agent selection
        agent_val = request.form.get("agent_id")
        agent_id = int(agent_val) if agent_val not in (None, "", "None") else None

        # Available to all
        available_to_all_val = request.form.get("available_to_all")
        available_to_all = 1 if available_to_all_val == "1" else 0

        # If available_to_all is checked, agent_id MUST be NULL
        if available_to_all == 1:
            agent_id = None

        # Branch selection
        branch_val = request.form.get("branch_id")
        branch_id = int(branch_val) if branch_val not in (None, "", "None") else None

        # Status
        status = request.form.get("status") or tx["status"] or "pending"

        # Recalculate USD amount with latest rate
        cur.execute(
            "SELECT rate FROM exchange_rates WHERE from_currency=? AND to_currency=? ORDER BY updated_at DESC LIMIT 1",
            (currency, "ZAR"))
        row = cur.fetchone()
        rate = float(row["rate"]) if row else 1.0
        amount_foreign = round(amount_local / rate, 6) if rate else amount_local

        # UPDATE ALL FIELDS
        cur.execute("""UPDATE transactions SET 
            sender_name=?, sender_phone=?, 
            receiver_name=?, receiver_phone=?,
            amount_local=?, amount_foreign=?, currency_code=?,
            payment_method=?, notes=?,
            agent_id=?, branch_id=?, status=?,
            available_to_all=?
            WHERE transaction_id=?""",
                    (sender_name, sender_phone, receiver_name, receiver_phone,
                     amount_local, amount_foreign, currency,
                     payment_method, notes,
                     agent_id, branch_id, status,
                     available_to_all, txid))
        db.commit()

        flash("Transaction updated successfully", "success")
        return redirect(url_for("admin.transactions"))


@admin_bp.route("/transactions/<string:txid>/delete", methods=["POST"])
@require_role("admin")
def delete_transaction(txid):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM transactions WHERE transaction_id=?", (txid,))
    db.commit(); flash("Transaction deleted", "success")
    return redirect(url_for("admin.transactions"))

# users & agents management
@admin_bp.route("/users")
@require_role("admin")
def users():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM users ORDER BY id DESC")
    users = cur.fetchall()
    return render_template("admin/users.html", users=users)


# Create user
@admin_bp.route("/users/create", methods=["GET", "POST"])
@require_role("admin")
def create_user():
    if request.method == "POST":
        # handle form save
        return redirect(url_for("admin.users"))
    return render_template("admin/create_agent.html")

@admin_bp.route("/agents")
@require_role("admin")
def agents():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT u.id, u.username, u.full_name, a.branch_id FROM users u LEFT JOIN agents a ON a.user_id=u.id WHERE u.role='agent' ORDER BY u.id DESC")
    agents = cur.fetchall()
    cur.execute("SELECT * FROM branches"); branches = cur.fetchall()
    return render_template("admin/agents.html", agents=agents, branches=branches)

@admin_bp.route("/agents/create", methods=["GET","POST"])
@require_role("admin")
def create_agent():
    db = get_db(); cur = db.cursor()
    if request.method=="POST":
        full_name = request.form.get("full_name"); username = request.form.get("username"); password = request.form.get("password")
        branch_id = request.form.get("branch_id") or None
        cur.execute("INSERT INTO users (full_name, username, password, role, branch_id) VALUES (?, ?, ?, ?, ?)",
                    (full_name, username, password, "agent", branch_id))
        user_id = cur.lastrowid
        cur.execute("INSERT INTO agents (user_id, branch_id) VALUES (?, ?)", (user_id, branch_id))
        db.commit(); flash("Agent created", "success"); return redirect(url_for("admin.agents"))
    cur.execute("SELECT * FROM branches"); branches = cur.fetchall()
    return render_template("admin/create_agent.html", branches=branches)

# branch CRUD (create exists earlier)
@admin_bp.route("/branches")
@require_role("admin")
def branches():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM branches ORDER BY id DESC"); branches = cur.fetchall()
    return render_template("admin/branches.html", branches=branches)


@admin_bp.route("/branches/create", methods=["GET", "POST"])
@require_role("admin")
def create_branch():
    db = get_db();
    cur = db.cursor()
    if request.method == "POST":
        name = request.form.get("name");
        location = request.form.get("location");
        rate_override = request.form.get("rate_override") or None
        cur.execute("INSERT INTO branches (name, location, rate_override) VALUES (?, ?, ?)",
                    (name, location, rate_override))
        db.commit();
        flash("Branch created", "success");
        return redirect(url_for("admin.branches"))

    # GET request - get latest exchange rate for reference
    cur.execute("SELECT rate FROM exchange_rates ORDER BY updated_at DESC LIMIT 1")
    latest_rate = cur.fetchone()
    return render_template("admin/edit_branch.html", branch=None, latest_rate=latest_rate)


@admin_bp.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
@require_role("admin")
def edit_branch(branch_id):
    db = get_db();
    cur = db.cursor()
    if request.method == "POST":
        cur.execute("UPDATE branches SET name=?, location=?, rate_override=? WHERE id=?",
                    (request.form.get("name"), request.form.get("location"),
                     request.form.get("rate_override") or None, branch_id))
        db.commit();
        flash("Branch updated", "success");
        return redirect(url_for("admin.branches"))

    cur.execute("SELECT * FROM branches WHERE id=?", (branch_id,))
    branch = cur.fetchone()

    # Get transaction count for this branch
    cur.execute("SELECT COUNT(*) as count FROM transactions WHERE branch_id=?", (branch_id,))
    tx_count = cur.fetchone()["count"]

    # Get latest exchange rate for reference
    cur.execute("SELECT rate FROM exchange_rates ORDER BY updated_at DESC LIMIT 1")
    latest_rate = cur.fetchone()

    return render_template("admin/edit_branch.html",
                           branch=branch,
                           branch_transaction_count=tx_count,
                           latest_rate=latest_rate)
@admin_bp.route("/branches/<int:branch_id>/delete", methods=["POST"])
@require_role("admin")
def delete_branch(branch_id):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM branches WHERE id=?", (branch_id,)); db.commit(); flash("Branch deleted", "success"); return redirect(url_for("admin.branches"))

# logs & reports
@admin_bp.route("/logs")
@require_role("admin")
def logs():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 500"); logs = cur.fetchall()
    return render_template("admin/logs.html", logs=logs)


# Edit user (GET shows form, POST saves)
@admin_bp.route("/users/<int:user_id>/edit", methods=["GET","POST"])
@require_role("admin")
def edit_user(user_id):
    db = get_db(); cur = db.cursor()
    if request.method == "POST":
        full_name = request.form.get("full_name")
        username = request.form.get("username")
        role = request.form.get("role")
        branch_id = request.form.get("branch_id") or None
        cur.execute("UPDATE users SET full_name=?, username=?, role=?, branch_id=? WHERE id=?",
                    (full_name, username, role, branch_id, user_id))
        db.commit()
        flash("User updated", "success")
        return redirect(url_for("admin.users"))
    # GET
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user = cur.fetchone()
    cur.execute("SELECT id, name FROM branches")
    branches = cur.fetchall()
    return render_template("admin/edit_user.html", user=user, branches=branches)

# Delete user
@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@require_role("admin")
def delete_user(user_id):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    # optional: cascade delete agents row if exists
    cur.execute("DELETE FROM agents WHERE user_id=?", (user_id,))
    db.commit()
    flash("User deleted", "info")
    return redirect(url_for("admin.users"))



# route: show breakdown (POST from form -> preview)
@admin_bp.route("/transactions/quote", methods=["POST"])
@require_role("admin")
def transaction_quote():
    db = get_db(); cur = db.cursor()
    # read amount safely
    raw_amount = request.form.get("amount_local") or request.form.get("amount") or "0"
    try:
        amount_local = float(raw_amount)
    except ValueError:
        flash("Invalid amount", "warning"); return redirect(url_for("admin.transactions"))

    currency = request.form.get("currency_code") or "ZAR"

    # fees
    pct = float(current_app.config.get("FEE_PERCENT", 0.01))
    flat = float(current_app.config.get("FEE_FLAT", 10.0))
    fee_percent = round(amount_local * pct, 2)
    subtotal = round(amount_local + fee_percent + flat, 2)

    # get latest rate for currency -> ZAR (fallback 1)
    cur.execute("SELECT rate FROM exchange_rates WHERE from_currency=? AND to_currency=? ORDER BY updated_at DESC LIMIT 1", (currency, "ZAR"))
    r = cur.fetchone()
    rate = float(r["rate"]) if r else 1.0
    amount_foreign = round(subtotal / rate, 6)

    breakdown = {
        "amount_local": amount_local,
        "fee_percent": fee_percent,
        "flat_fee": flat,
        "subtotal": subtotal,
        "rate": rate,
        "amount_foreign": amount_foreign,
        "currency": currency
    }

    # keep other form fields so Confirm POST can reuse them
    return render_template("admin/quote.html", breakdown=breakdown, form=request.form)





@admin_bp.route("/reports")
@require_role("admin")
def reports_main():
    db = get_db(); cur = db.cursor()
    cur.execute("""SELECT date(timestamp) as day, COUNT(*) as ct, IFNULL(SUM(amount_local),0) as total
                   FROM transactions GROUP BY date(timestamp) ORDER BY day DESC LIMIT 30""")
    daily = cur.fetchall()

    cur.execute("""SELECT strftime('%Y-%m', timestamp) as month, COUNT(*) as ct, IFNULL(SUM(amount_local),0) as total
                   FROM transactions GROUP BY month ORDER BY month DESC LIMIT 24""")
    monthly = cur.fetchall()

    cur.execute("""SELECT strftime('%Y', timestamp) as year, COUNT(*) as ct, IFNULL(SUM(amount_local),0) as total
                   FROM transactions GROUP BY year ORDER BY year DESC""")
    yearly = cur.fetchall()

    return render_template("admin/reports.html", daily=daily, monthly=monthly, yearly=yearly)


# Add these imports at the top
from datetime import datetime, timedelta


@admin_bp.route("/reports/daily")
@require_role("admin")
def reports_daily():
    db = get_db()
    cur = db.cursor()

    # Get selected date from query parameter
    selected_date = request.args.get('date')

    # Get today's summary
    if selected_date:
        cur.execute("""
            SELECT COUNT(*) as count, IFNULL(SUM(amount_local), 0) as total
            FROM transactions WHERE date(timestamp)=?
        """, (selected_date,))
    else:
        cur.execute("""
            SELECT COUNT(*) as count, IFNULL(SUM(amount_local), 0) as total
            FROM transactions WHERE date(timestamp)=date('now')
        """)
    today = cur.fetchone()

    # Get transactions for selected date or today
    if selected_date:
        cur.execute("""
            SELECT t.*, u.full_name as agent_name 
            FROM transactions t 
            LEFT JOIN users u ON t.agent_id=u.id 
            WHERE date(t.timestamp)=? 
            ORDER BY t.timestamp DESC
        """, (selected_date,))
    else:
        cur.execute("""
            SELECT t.*, u.full_name as agent_name 
            FROM transactions t 
            LEFT JOIN users u ON t.agent_id=u.id 
            WHERE date(t.timestamp)=date('now') 
            ORDER BY t.timestamp DESC
        """)
    rows = cur.fetchall()

    # Get last 7 days summary for chart
    cur.execute("""
        SELECT date(timestamp) as day, 
               COUNT(*) as count, 
               IFNULL(SUM(amount_local), 0) as total
        FROM transactions 
        WHERE date(timestamp) >= date('now', '-7 days')
        GROUP BY date(timestamp) 
        ORDER BY day DESC
    """)
    daily_summary = cur.fetchall()

    return render_template("admin/reports_daily.html",
                           today=today,
                           rows=rows,
                           daily_summary=daily_summary,
                           selected_date=selected_date)


@admin_bp.route("/reports/monthly")
@require_role("admin")
def reports_monthly():
    db = get_db()
    cur = db.cursor()

    selected_year = request.args.get('year')
    selected_month = request.args.get('month')

    # Build query based on filters
    where_clause = ""
    params = []

    if selected_year:
        where_clause = " WHERE strftime('%Y', timestamp) = ?"
        params.append(selected_year)

        if selected_month:
            where_clause += " AND strftime('%m', timestamp) = ?"
            params.append(selected_month)
    else:
        # Default to last 12 months
        where_clause = " WHERE timestamp >= date('now', '-12 months')"

    # Get monthly summary
    query = f"""
        SELECT strftime('%Y-%m', timestamp) as month, 
               COUNT(*) as count, 
               IFNULL(SUM(amount_local), 0) as total
        FROM transactions
        {where_clause}
        GROUP BY month 
        ORDER BY month DESC
    """
    cur.execute(query, params)
    rows = cur.fetchall()

    # Get available years for filter dropdown
    cur.execute("SELECT DISTINCT strftime('%Y', timestamp) as year FROM transactions ORDER BY year DESC")
    available_years_result = cur.fetchall()
    available_years = [row['year'] for row in available_years_result]

    return render_template("admin/reports_monthly.html",
                           rows=rows,
                           selected_year=selected_year,
                           selected_month=selected_month,
                           available_years=available_years)


@admin_bp.route("/reports/yearly")
@require_role("admin")
def reports_yearly():
    db = get_db()
    cur = db.cursor()

    start_year = request.args.get('start_year')
    end_year = request.args.get('end_year')

    # Build query based on filters
    where_clause = ""
    params = []

    if start_year:
        where_clause = " WHERE strftime('%Y', timestamp) >= ?"
        params.append(start_year)

        if end_year:
            where_clause += " AND strftime('%Y', timestamp) <= ?"
            params.append(end_year)

    # Get yearly summary
    query = f"""
        SELECT strftime('%Y', timestamp) as year, 
               COUNT(*) as count, 
               IFNULL(SUM(amount_local), 0) as total
        FROM transactions
        {where_clause}
        GROUP BY year 
        ORDER BY year DESC
    """
    cur.execute(query, params)
    rows = cur.fetchall()

    # Get available years for filter dropdown
    cur.execute("SELECT DISTINCT strftime('%Y', timestamp) as year FROM transactions ORDER BY year DESC")
    available_years_result = cur.fetchall()
    available_years = [row['year'] for row in available_years_result]

    return render_template("admin/reports_yearly.html",
                           rows=rows,
                           start_year=start_year,
                           end_year=end_year,
                           available_years=available_years)


@admin_bp.route("/rates", methods=["GET", "POST"])
@require_role("admin")
def rates():
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        if "set_rate" in request.form:
            try:
                r = float(request.form.get("rate"))
                source = request.form.get("source") or "Manual"
                cur.execute("""
                    INSERT INTO exchange_rates (from_currency, to_currency, rate, source, updated_at) 
                    VALUES (?, ?, ?, ?, datetime('now'))
                """, ("USD", "ZAR", r, source))
                db.commit()
                flash(f"Manual rate saved: USD->ZAR = {r}", "success")
            except ValueError:
                flash("Invalid rate", "danger")

        elif "toggle_auto" in request.form:
            current = get_setting("auto_update_rates") or "true"
            newv = "false" if current.lower() == "true" else "true"
            set_setting("auto_update_rates", newv)
            flash(f"Auto-update {'enabled' if newv == 'true' else 'disabled'}", "info")

        elif "fetch_now" in request.form:
            from .rates import update_usd_zar
            res = update_usd_zar()
            if res.get("ok"):
                flash(f"Rates updated: USD->ZAR = {res['rate']} (Source: {res.get('source', 'unknown')})", "success")
            else:
                flash(f"Could not fetch rates: {res.get('error')}", "danger")

        elif "clear_history" in request.form:
            # Keep only the latest rate
            cur.execute("""
                DELETE FROM exchange_rates 
                WHERE id NOT IN (
                    SELECT id FROM exchange_rates 
                    ORDER BY updated_at DESC 
                    LIMIT 1
                )
            """)
            db.commit()
            flash("Rate history cleared, keeping only latest rate", "info")

        return redirect(url_for("admin.rates"))

    # GET request
    latest = get_latest_rate()
    auto = get_setting("auto_update_rates") or "true"

    # Get rate history for chart
    cur.execute("""
        SELECT rate, updated_at, source
        FROM exchange_rates 
        WHERE from_currency='USD' AND to_currency='ZAR' 
        ORDER BY updated_at DESC 
        LIMIT 50
    """)
    history = cur.fetchall()

    # Get auto-update status
    from .rates import should_update_rates
    needs_update = should_update_rates()

    return render_template("admin/rates.html",
                           latest=latest,
                           auto=(auto == "true"),
                           history=history,
                           needs_update=needs_update)


@admin_bp.route("/rates/fetch_now", methods=["POST"])
@require_role("admin")
def rates_fetch_now():  # Renamed from rates() to rates_fetch_now()
    """Separate endpoint for fetching rates via AJAX or direct POST"""
    from .rates import update_usd_zar
    res = update_usd_zar()
    if res.get("ok"):
        flash(f"Rates updated: USD->ZAR = {res['rate']} (Source: {res.get('source', 'unknown')})", "success")
    else:
        flash(f"Could not fetch rates: {res.get('error')}", "danger")
    return redirect(url_for("admin.rates"))


@admin_bp.route("/agents/<int:agent_id>/delete", methods=["POST"])
@require_role("admin")
def delete_agent(agent_id):
    db = get_db()
    cur = db.cursor()

    try:
        # First delete from agents table (if exists)
        cur.execute("DELETE FROM agents WHERE user_id=?", (agent_id,))

        # Then delete from users table
        cur.execute("DELETE FROM users WHERE id=?", (agent_id,))

        db.commit()
        flash("Agent deleted successfully", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error deleting agent: {str(e)}", "danger")

    return redirect(url_for("admin.agents"))


@admin_bp.route("/transactions/<txid>")
@require_role("admin")
def view_transaction(txid):
    """View transaction details including who completed it"""
    db = get_db()
    cur = db.cursor()

    # Get transaction with all user info
    cur.execute("""
        SELECT 
            t.*,
            u1.full_name as agent_name,
            u1.username as agent_username,
            u2.full_name as completed_by_name,
            u2.username as completed_by_username,
            u3.full_name as created_by_name,
            b.name as branch_name
        FROM transactions t 
        LEFT JOIN users u1 ON t.agent_id = u1.id
        LEFT JOIN users u2 ON t.completed_by = u2.id
        LEFT JOIN users u3 ON t.created_by = u3.id
        LEFT JOIN branches b ON t.branch_id = b.id
        WHERE t.transaction_id = ?
    """, (txid,))

    tx = cur.fetchone()

    if not tx:
        flash("Transaction not found", "warning")
        return redirect(url_for("admin.transactions"))

    # Get completion log
    cur.execute("""
        SELECT * FROM logs 
        WHERE details LIKE ? 
        AND (action = 'completed_tx' OR action = 'sms_sent')
        ORDER BY id DESC
    """, (f"%{txid}%",))

    logs = cur.fetchall()

    return render_template("admin/view_transaction.html", tx=tx, logs=logs)


@admin_bp.route("/transactions/<txid>/verify", methods=["POST"])
@require_role("admin")
def verify_transaction(txid):
    """Admin verifies a completed transaction"""
    db = get_db()
    cur = db.cursor()
    admin_id = session.get("user_id")

    try:
        # Get admin name
        cur.execute("SELECT full_name FROM users WHERE id=?", (admin_id,))
        admin = cur.fetchone()
        admin_name = admin['full_name'] if admin else f"Admin {admin_id}"

        # Update transaction with verification info
        cur.execute("""
            UPDATE transactions 
            SET verified_by=?, 
                verified_at=datetime('now')
            WHERE transaction_id=? 
            AND status='completed'
        """, (admin_id, txid))

        if cur.rowcount == 0:
            flash("Transaction not found or not completed", "warning")
            return redirect(url_for("admin.transactions"))

        # Log the verification
        cur.execute("""
            INSERT INTO logs (user_id, action, details) 
            VALUES (?, ?, ?)
        """, (admin_id, "verified_tx", f"{txid} verified by {admin_name}"))

        db.commit()

        flash(f"Transaction {txid} verified by {admin_name}", "success")

    except Exception as e:
        db.rollback()
        flash(f"Error verifying transaction: {str(e)}", "danger")

    return redirect(url_for("admin.view_transaction", txid=txid))


@admin_bp.route("/transactions/<txid>/complete", methods=["POST"])
@require_role("admin")
def mark_completed(txid):
    """Admin marks a transaction as completed"""
    db = get_db()
    cur = db.cursor()
    admin_id = session.get("user_id")

    try:
        # Get admin name
        cur.execute("SELECT full_name FROM users WHERE id=?", (admin_id,))
        admin = cur.fetchone()
        admin_name = admin['full_name'] if admin else f"Admin {admin_id}"

        # Update transaction
        cur.execute("""
            UPDATE transactions 
            SET status='completed', 
                completed_by=?, 
                completed_at=datetime('now'),
                timestamp=datetime('now')
            WHERE transaction_id=?
        """, (admin_id, txid))

        if cur.rowcount == 0:
            flash("Transaction not found", "warning")
            return redirect(url_for("admin.transactions"))

        # Log
        cur.execute("""
            INSERT INTO logs (user_id, action, details) 
            VALUES (?, ?, ?)
        """, (admin_id, "admin_completed_tx", f"{txid} marked completed by admin {admin_name}"))

        db.commit()

        flash(f"Transaction {txid} marked as completed by admin", "success")

    except Exception as e:
        db.rollback()
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for("admin.view_transaction", txid=txid))


@admin_bp.route("/transactions/debug-create", methods=["GET", "POST"])
@require_role("admin")
def debug_create_transaction():
    """Debug endpoint to test transaction creation"""
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        try:
            # Get form data
            sender_name = request.form.get("sender_name", "Test Sender")
            amount_local = float(request.form.get("amount_local", 1000))
            txid = f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            print(f"DEBUG: Creating transaction {txid}")
            print(f"DEBUG: Sender: {sender_name}, Amount: {amount_local}")

            # Simple test insert
            cur.execute("""
                INSERT INTO transactions
                (transaction_id, sender_name, amount_local, amount_foreign, 
                 currency_code, status, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                txid, sender_name, amount_local, amount_local / 18.5,  # Simple USD conversion
                "ZAR", "pending", session.get("user_id")
            ))
            db.commit()

            # Verify it was inserted
            cur.execute("SELECT COUNT(*) as cnt FROM transactions WHERE transaction_id = ?", (txid,))
            count = cur.fetchone()["cnt"]

            return f"""
            <h1>Debug Test Result</h1>
            <p>Transaction ID: {txid}</p>
            <p>Inserted successfully: {'YES' if count > 0 else 'NO'}</p>
            <p>Rows in database with this ID: {count}</p>
            <p><a href="{{ url_for('admin.transactions') }}">Check transactions list</a></p>
            """

        except Exception as e:
            db.rollback()
            return f"<h1>Error</h1><pre>{str(e)}</pre>"

    # GET request - show simple form
    return '''
    <form method="POST">
        <input type="text" name="sender_name" placeholder="Sender Name" value="Test Debug"><br>
        <input type="number" name="amount_local" placeholder="Amount" value="1000"><br>
        <button type="submit">Test Create Transaction</button>
    </form>
    '''

# Function to update the dollar balance
def update_dollar_balance(db, amount_foreign, operation='subtract'):
    """
    Updates the system's total USD balance.
    operation: 'subtract' for sending/paying out, 'add' for receiving.
    """
    cur = db.cursor()
    try:
        cur.execute("SELECT current_balance FROM dollar_balance WHERE id = 1")
        row = cur.fetchone()
        current = row['current_balance'] if row else 0.0

        if operation == 'subtract':
            new_balance = current - amount_foreign
        elif operation == 'add':
            new_balance = current + amount_foreign
        else:
            raise ValueError("Operation must be 'add' or 'subtract'")

        # Update the balance and timestamp
        cur.execute("""
            UPDATE dollar_balance 
            SET current_balance = ?, last_updated = datetime('now')
            WHERE id = 1
        """, (new_balance,))
        db.commit()
        return new_balance
    except Exception as e:
        db.rollback()
        raise e


# Add these imports at the top
import json
from flask import jsonify


# In your admin.py - Add this route
@admin_bp.route('/api/dashboard-balance')
def dashboard_balance():
    """Fast endpoint specifically for dashboard balance updates"""
    try:
        db = get_db()
        cursor = db.cursor()

        # DIRECT QUERY - Same as your manage_dollar page
        cursor.execute("""
            SELECT current_balance, last_updated 
            FROM dollar_balance 
            ORDER BY id DESC 
            LIMIT 1
        """)

        balance_data = cursor.fetchone()

        if balance_data:
            return jsonify({
                'success': True,
                'balance': float(balance_data['current_balance']),
                'last_updated': balance_data['last_updated'],
                'timestamp': datetime.now().isoformat()
            })
        else:
            # Fallback if no balance record exists
            return jsonify({
                'success': True,
                'balance': 0.00,
                'last_updated': None,
                'timestamp': datetime.now().isoformat()
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'balance': 0.00
        }), 500
# Add these routes somewhere in your admin.py

@admin_bp.route("/api/dollar_balance")
@require_role("admin")
def get_dollar_balance():
    """API endpoint to get current USD balance"""
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("SELECT current_balance, last_updated FROM dollar_balance WHERE id = 1")
        row = cur.fetchone()

        if row:
            return jsonify({
                'balance': float(row['current_balance']),
                'last_updated': row['last_updated'],
                'currency': 'USD'
            })
        else:
            return jsonify({'error': 'Balance not found'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_bp.route("/api/dollar_balance/history")
@require_role("admin")
def get_balance_history():
    """API endpoint to get recent balance changes"""
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT 
                dbl.*,
                t.sender_name,
                t.receiver_name,
                t.amount_foreign
            FROM dollar_balance_log dbl
            LEFT JOIN transactions t ON dbl.transaction_id = t.transaction_id
            ORDER BY dbl.timestamp DESC
            LIMIT 10
        """)

        history = cur.fetchall()

        formatted_history = []
        for entry in history:
            formatted_history.append({
                'id': entry['id'],
                'transaction_id': entry['transaction_id'],
                'change_amount': float(entry['change_amount']),
                'previous_balance': float(entry['previous_balance']),
                'new_balance': float(entry['new_balance']),
                'change_type': entry['change_type'],
                'description': entry['description'],
                'timestamp': entry['timestamp'],
                'sender_name': entry['sender_name'],
                'receiver_name': entry['receiver_name'],
                'transaction_amount': float(entry['amount_foreign']) if entry['amount_foreign'] else None
            })

        return jsonify({'history': formatted_history})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route("/dollar_balance_logs")
@require_role("admin")
def dollar_balance_logs():
    """Display all dollar balance logs with filtering"""
    db = get_db()
    cur = db.cursor()

    # Build query with filters
    query = """
        SELECT 
            dbl.*,
            u.username as created_by_user,
            u.full_name as created_by_full_name,
            t.sender_name,
            t.receiver_name,
            t.amount_foreign as transaction_amount
        FROM dollar_balance_log dbl
        LEFT JOIN users u ON dbl.created_by = u.id
        LEFT JOIN transactions t ON dbl.transaction_id = t.transaction_id
        WHERE 1=1
    """

    params = []

    # Date filters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if start_date:
        query += " AND date(dbl.timestamp) >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date(dbl.timestamp) <= ?"
        params.append(end_date)

    # Change type filter
    change_type = request.args.get('change_type')
    if change_type:
        query += " AND dbl.change_type = ?"
        params.append(change_type)

    # Description filter
    description = request.args.get('description')
    if description:
        query += " AND dbl.description LIKE ?"
        params.append(f"%{description}%")

    # Transaction ID filter
    transaction_id = request.args.get('transaction_id')
    if transaction_id:
        query += " AND dbl.transaction_id LIKE ?"
        params.append(f"%{transaction_id}%")

    # Sorting (always by timestamp desc for now)
    query += " ORDER BY dbl.timestamp DESC"

    # Execute query
    cur.execute(query, params)
    logs = cur.fetchall()

    # Calculate statistics
    total_inflow = 0
    total_outflow = 0

    for log in logs:
        amount = float(log['change_amount'])
        if amount > 0:
            total_inflow += amount
        elif amount < 0:
            total_outflow += amount  # This is negative, so we'll format it separately

    net_change = total_inflow + total_outflow  # outflow is negative

    return render_template("admin/dollar_balance_logs.html",
                           logs=logs,
                           total_inflow=total_inflow,
                           total_outflow=total_outflow,
                           net_change=net_change)


@admin_bp.route("/api/dollar_balance/log/<int:log_id>")
@require_role("admin")
def get_dollar_balance_log(log_id):
    """Get details of a specific balance log"""
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT 
                dbl.*,
                u.username as created_by_user,
                u.full_name as created_by_full_name,
                t.sender_name,
                t.receiver_name,
                t.amount_foreign as transaction_amount
            FROM dollar_balance_log dbl
            LEFT JOIN users u ON dbl.created_by = u.id
            LEFT JOIN transactions t ON dbl.transaction_id = t.transaction_id
            WHERE dbl.id = ?
        """, (log_id,))

        log = cur.fetchone()

        if log:
            # Format the data
            formatted_log = {
                'id': log['id'],
                'transaction_id': log['transaction_id'],
                'change_amount': float(log['change_amount']),
                'previous_balance': float(log['previous_balance']),
                'new_balance': float(log['new_balance']),
                'change_type': log['change_type'],
                'description': log['description'],
                'timestamp': log['timestamp'],
                'created_by': log['created_by'],
                'created_by_user': {
                    'username': log['created_by_user'],
                    'full_name': log['created_by_full_name']
                } if log['created_by_user'] else None,
                'transaction_details': {
                    'sender_name': log['sender_name'],
                    'receiver_name': log['receiver_name'],
                    'amount': float(log['transaction_amount']) if log['transaction_amount'] else None
                } if log['sender_name'] else None
            }

            return jsonify({'log': formatted_log})
        else:
            return jsonify({'error': 'Log not found'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route("/api/dollar_balance/export")
@require_role("admin")
def export_dollar_balance_logs():
    """Export balance logs to CSV"""
    db = get_db()
    cur = db.cursor()

    # Get filters from request
    params = []
    query = """
        SELECT 
            dbl.timestamp,
            dbl.transaction_id,
            dbl.change_type,
            dbl.description,
            dbl.change_amount,
            dbl.previous_balance,
            dbl.new_balance,
            u.username as created_by
        FROM dollar_balance_log dbl
        LEFT JOIN users u ON dbl.created_by = u.id
        WHERE 1=1
    """

    # Apply same filters as main route
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if start_date:
        query += " AND date(dbl.timestamp) >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date(dbl.timestamp) <= ?"
        params.append(end_date)

    change_type = request.args.get('change_type')
    if change_type:
        query += " AND dbl.change_type = ?"
        params.append(change_type)

    query += " ORDER BY dbl.timestamp DESC"

    cur.execute(query, params)
    logs = cur.fetchall()

    # Create CSV content
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        'Date/Time', 'Transaction ID', 'Type', 'Description',
        'Change Amount', 'Previous Balance', 'New Balance', 'Created By'
    ])

    # Write data
    for log in logs:
        writer.writerow([
            log['timestamp'],
            log['transaction_id'] or '',
            log['change_type'].replace('_', ' ').title(),
            log['description'] or '',
            f"${log['change_amount']:.2f}",
            f"${log['previous_balance']:.2f}",
            f"${log['new_balance']:.2f}",
            log['created_by'] or ''
        ])

    # Create response
    from flask import Response

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment;filename=dollar_balance_logs.csv",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )


@admin_bp.route("/dollar_balance/manage", methods=["GET", "POST"])
@require_role("admin")
def manage_dollar_balance():
    """Page to manually adjust dollar balance"""
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        amount = float(request.form.get("amount", 0))
        notes = request.form.get("notes", "")

        if amount <= 0:
            flash("Amount must be greater than 0", "warning")
            return redirect(url_for("admin.manage_dollar_balance"))

        try:
            # Get current balance
            cur.execute("SELECT current_balance FROM dollar_balance WHERE id = 1")
            row = cur.fetchone()
            current_balance = float(row['current_balance']) if row else 0.0

            if action == "add":
                new_balance = current_balance + amount
                change_amount = amount
                description = f"Manual addition: {notes}"
                change_type = "manual_adjustment"
            elif action == "subtract":
                if amount > current_balance:
                    flash(f"Cannot deduct ${amount:.2f} - current balance is only ${current_balance:.2f}", "danger")
                    return redirect(url_for("admin.manage_dollar_balance"))
                new_balance = current_balance - amount
                change_amount = -amount
                description = f"Manual deduction: {notes}"
                change_type = "manual_adjustment"
            else:
                flash("Invalid action", "danger")
                return redirect(url_for("admin.manage_dollar_balance"))

            # Update balance
            cur.execute("""
                UPDATE dollar_balance 
                SET current_balance = ?, last_updated = datetime('now')
                WHERE id = 1
            """, (new_balance,))

            # Log the change
            cur.execute("""
                INSERT INTO dollar_balance_log 
                (change_amount, previous_balance, new_balance, change_type, description, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                change_amount,
                current_balance,
                new_balance,
                change_type,
                description,
                session.get("user_id")
            ))

            db.commit()

            flash(f"✅ Balance updated successfully! ${current_balance:.2f} → ${new_balance:.2f}", "success")

        except Exception as e:
            db.rollback()
            flash(f"Error updating balance: {str(e)}", "danger")

        return redirect(url_for("admin.manage_dollar_balance"))

    # GET request - show current balance and recent logs
    cur.execute("SELECT current_balance, last_updated FROM dollar_balance WHERE id = 1")
    balance = cur.fetchone()

    # Get recent manual adjustments
    cur.execute("""
        SELECT * FROM dollar_balance_log 
        WHERE change_type IN ('manual_adjustment', 'correction')
        ORDER BY timestamp DESC 
        LIMIT 10
    """)
    recent_logs = cur.fetchall()

    return render_template("admin/manage_dollar_balance.html",
                           balance=balance,
                           recent_logs=recent_logs)


@admin_bp.route("/test-form", methods=["GET", "POST"])
def test_form():
    """Test route to debug form submission"""
    if request.method == "POST":
        # Log everything
        print("\n" + "=" * 60)
        print("FORM SUBMISSION TEST RESULTS")
        print("=" * 60)
        print(f"All form data: {dict(request.form)}")
        print(f"available_to_all: '{request.form.get('available_to_all')}'")
        print(f"agent_id: '{request.form.get('agent_id')}'")
        print(f"amount_local: '{request.form.get('amount_local')}'")
        print("=" * 60 + "\n")

        return f"""
        <!DOCTYPE html>
        <html>
        <head><title>Test Results</title></head>
        <body style="padding: 20px; font-family: monospace;">
            <h2>Form Submission Results</h2>
            <pre>{dict(request.form)}</pre>
            <hr>
            <h3>Key Values:</h3>
            <ul>
                <li><strong>available_to_all</strong>: '{request.form.get('available_to_all')}'</li>
                <li><strong>agent_id</strong>: '{request.form.get('agent_id')}'</li>
                <li><strong>amount_local</strong>: '{request.form.get('amount_local')}'</li>
            </ul>
            <a href="/admin/test-form">Test Again</a> | 
            <a href="/admin/transactions/create">Go to Real Form</a>
        </body>
        </html>
        """

    # GET request - show test form
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Form</title>
        <style>
            body { padding: 20px; font-family: sans-serif; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; }
            input, select { padding: 8px; width: 300px; }
            .debug { background: #f0f0f0; padding: 10px; margin: 10px 0; }
        </style>
    </head>
    <body>
        <h2>Test Form Submission</h2>

        <form method="POST">
            <div class="form-group">
                <label>Sender Name:</label>
                <input type="text" name="sender_name" value="Test Sender">
            </div>

            <div class="form-group">
                <label>Amount (ZAR):</label>
                <input type="number" name="amount_local" value="1000">
            </div>

            <div class="form-group">
                <label>Agent:</label>
                <select name="agent_id">
                    <option value="">-- None --</option>
                    <option value="1">Agent 1</option>
                    <option value="2">Agent 2</option>
                    <option value="3">Agent 3</option>
                </select>
            </div>

            <div class="form-group">
                <strong>Available to All:</strong><br>
                <input type="radio" name="available_to_all" id="test_yes" value="1">
                <label for="test_yes">Yes (available to all agents)</label><br>

                <input type="radio" name="available_to_all" id="test_no" value="0" checked>
                <label for="test_no">No (assign to specific agent)</label>
            </div>

            <button type="submit">Submit Test Form</button>
        </form>

        <div class="debug">
            <strong>Debug Info:</strong>
            <p>This form uses radio buttons instead of checkboxes.</p>
            <p>Radio buttons ALWAYS submit a value (either "0" or "1").</p>
        </div>
    </body>
    </html>
    '''