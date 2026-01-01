import json
from datetime import datetime, timedelta
from flask import Blueprint, render_template, session, redirect, request, url_for, flash, current_app, jsonify
from sqlalchemy import func, desc, or_, and_, extract, cast, Date
from sqlalchemy.sql import text

from .helpers import generate_unique_txid
from .sms import send_sms, build_sms_template
from .utils import require_role, update_rate_if_needed, get_latest_rate, set_setting, get_setting
from .models import db, User, Transaction, Branch, Currency, ExchangeRate, Setting, DollarBalance, Log, Notification, \
    Agent
from .rates import update_usd_zar

admin_bp = Blueprint("admin", __name__, template_folder="templates")


@admin_bp.route("/dashboard")
@require_role("admin")
def dashboard():
    # stats
    total_transactions = Transaction.query.count()
    total_agents = User.query.filter_by(role='agent').count()

    # Today's volume
    today = datetime.utcnow().date()
    today_volume_result = db.session.query(func.coalesce(func.sum(Transaction.amount_local), 0)).filter(
        cast(Transaction.timestamp, Date) == today
    ).first()
    today_volume = float(today_volume_result[0]) if today_volume_result else 0.0

    # charts last 7 days
    seven_days_ago = today - timedelta(days=7)
    daily_stats = db.session.query(
        cast(Transaction.timestamp, Date).label('d'),
        func.coalesce(func.sum(Transaction.amount_local), 0).label('s')
    ).filter(
        cast(Transaction.timestamp, Date) >= seven_days_ago
    ).group_by(
        cast(Transaction.timestamp, Date)
    ).order_by(text('d DESC')).limit(7).all()

    labels = [row.d.strftime('%Y-%m-%d') for row in reversed(daily_stats)]
    data = [float(row.s) for row in reversed(daily_stats)]

    # rates: update if needed (auto)
    latest = update_rate_if_needed(force=False)
    usd_zar = latest.get('rate') if latest else None

    stats = {
        "total_transactions": total_transactions,
        "total_agents": total_agents,
        "today_volume": today_volume
    }
    charts = {"transfers": {"labels": labels, "data": data}}

    return render_template("admin/dashboard.html", stats=stats, charts=charts, usd_zar=usd_zar)

# transactions list & management
@admin_bp.route("/transactions")
@require_role("admin")
def transactions():
    # Get search parameters
    txid_suffix = request.args.get('txid', '').upper().strip()
    status = request.args.get('status', '')

    # Simple query - no joins
    query = Transaction.query

    # Add TXID filter (exact match for suffix)
    if txid_suffix:
        query = query.filter(Transaction.transaction_id == 'ISA-' + txid_suffix)

    # Add status filter
    if status:
        query = query.filter(Transaction.status == status)

    # Always order by most recent first
    txs = query.order_by(Transaction.timestamp.desc()).all()

    return render_template("admin/transactions.html", txs=txs)

@admin_bp.route("/transactions/create", methods=["GET", "POST"])
@require_role("admin")
def create_transaction():
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

            # Available to all agents
            available_to_all_val = request.form.get("available_to_all")
            available_to_all = True if available_to_all_val == "1" else False

            # Agent selection - FIXED VERSION
            agent_val = request.form.get("agent_id")

            # ✅ CRITICAL FIX: If available_to_all is checked, agent_id MUST be NULL
            if available_to_all:
                agent_id = None  # Force NULL for available_to_all transactions
                print(f"DEBUG: available_to_all=True, so forcing agent_id to NULL")
            else:
                agent_id = int(agent_val) if agent_val not in (None, "", "None") else None
                print(f"DEBUG: available_to_all=False, using agent_id: {agent_id}")

            # Branch selection
            branch_val = request.form.get("branch_id")
            branch_id = int(branch_val) if branch_val not in (None, "", "None") else None

            # Status
            status = request.form.get("status") or "pending"

            # Get exchange rate
            exchange_rate = ExchangeRate.query.filter_by(
                from_currency=currency_code,
                to_currency="ZAR"
            ).order_by(ExchangeRate.updated_at.desc()).first()

            rate = float(exchange_rate.rate) if exchange_rate else 1.0
            if rate == 0:
                rate = 1.0
            amount_foreign = round(amount_local / rate, 6)

            # Generate unique transaction ID
            txid = generate_unique_txid()

            try:
                # First, check if we have enough balance (only if subtracting)
                dollar_balance = DollarBalance.query.first()
                current_balance = float(dollar_balance.current_balance) if dollar_balance else 0.0

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

                # Create transaction
                tx = Transaction(
                    transaction_id=txid,
                    sender_name=sender_name,
                    sender_phone=sender_phone,
                    receiver_name=receiver_name,
                    receiver_phone=receiver_phone,
                    amount_local=amount_local,
                    amount_foreign=amount_foreign,
                    currency_code=currency_code,
                    status=status,
                    created_by=session.get("user_id"),
                    agent_id=agent_id,
                    branch_id=branch_id,
                    token=None,
                    available_to_all=available_to_all,
                    payment_method=payment_method,
                    notes=notes,
                    timestamp=datetime.utcnow()
                )

                db.session.add(tx)
                db.session.flush()  # Get the ID without committing
                transaction_created = True

                # ✅ NEW: Update dollar balance after successful transaction creation
                try:
                    # Calculate new balance (subtract for outgoing transactions)
                    new_balance = current_balance - amount_foreign

                    # Update main balance
                    if dollar_balance:
                        dollar_balance.current_balance = new_balance
                        dollar_balance.last_updated = datetime.utcnow()
                    else:
                        dollar_balance = DollarBalance(
                            current_balance=new_balance,
                            last_updated=datetime.utcnow()
                        )
                        db.session.add(dollar_balance)

                    print(
                        f"DEBUG: Dollar balance updated: ${current_balance:.2f} → ${new_balance:.2f} (-${amount_foreign:.2f})")

                except Exception as balance_error:
                    # Don't rollback the transaction, just log the balance update error
                    print(f"WARNING: Could not update dollar balance: {balance_error}")
                    log = Log(
                        user_id=session.get("user_id"),
                        action="balance_update_error",
                        details=f"Failed to update balance for {txid}: {str(balance_error)[:200]}"
                    )
                    db.session.add(log)
                    new_balance = current_balance  # Keep old balance if update failed

                db.session.commit()

            except Exception as e:
                db.session.rollback()
                flash(f"Error creating transaction: {str(e)}", "danger")
                return redirect(url_for("admin.transactions"))

            # SMS notification if receiver phone provided
            if receiver_phone and transaction_created:
                # Get agent name for SMS
                if agent_id:
                    agent = User.query.get(agent_id)
                    agent_display = agent.full_name if agent else f"ID:{agent_id}"
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
                    log = Log(
                        user_id=session.get("user_id"),
                        action="sms_sent",
                        details=f"To: {receiver_phone} - {str(resp)[:200]}"
                    )
                    db.session.add(log)
                    db.session.commit()
                except Exception as e:
                    # Log error but don't fail transaction
                    log = Log(
                        user_id=session.get("user_id"),
                        action="sms_error",
                        details=f"Failed to send SMS: {str(e)[:200]}"
                    )
                    db.session.add(log)
                    db.session.commit()

            # Calculate quote for success message
            pct = float(current_app.config.get("FEE_PERCENT", 0.01))
            flat = float(current_app.config.get("FEE_FLAT", 10.0))
            fee_percent = round(amount_local * pct, 2)
            subtotal = round(amount_local + fee_percent + flat, 2)

            # Get final balance for display
            final_balance = DollarBalance.query.first()
            final_balance_amount = float(final_balance.current_balance) if final_balance else 0.0

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
            • New System Balance: ${final_balance_amount:.2f}
            """, "success")

            return redirect(url_for("admin.transactions"))

        # If not confirmed, show form again
        return redirect(url_for("admin.create_transaction"))

    # GET request - show the form
    agents = User.query.filter_by(role='agent').all()
    branches = Branch.query.all()
    currencies = Currency.query.all()

    return render_template("admin/create_transaction.html",
                           agents=agents,
                           branches=branches,
                           currencies=currencies)


@admin_bp.route("/transactions/<txid>/edit", methods=["GET", "POST"])
@require_role("admin")
def edit_transaction(txid):
    tx = Transaction.query.filter_by(transaction_id=txid).first()
    if not tx:
        flash("Transaction not found", "warning")
        return redirect(url_for("admin.transactions"))

    # GET request - show form
    if request.method == "GET":
        # Get data for dropdowns (same as create)
        agents = User.query.filter_by(role='agent').all()
        branches = Branch.query.all()
        currencies = Currency.query.all()

        return render_template("admin/edit_transaction.html",
                               tx=tx, agents=agents, branches=branches, currencies=currencies)

    # POST request - update transaction
    if request.method == "POST":
        # Get all form fields (matching create form)
        tx.sender_name = request.form.get("sender_name") or tx.sender_name
        tx.sender_phone = request.form.get("sender_phone") or tx.sender_phone
        tx.receiver_name = request.form.get("receiver_name") or tx.receiver_name
        tx.receiver_phone = request.form.get("receiver_phone") or tx.receiver_phone
        tx.payment_method = request.form.get("payment_method") or tx.payment_method or "cash"
        tx.notes = request.form.get("notes") or tx.notes or ""

        # Amount
        raw_amount = request.form.get("amount_local")
        if raw_amount is not None and raw_amount != "":
            try:
                tx.amount_local = float(raw_amount)
            except ValueError:
                flash("Invalid amount", "warning")
                return redirect(url_for("admin.edit_transaction", txid=txid))

        currency = request.form.get("currency_code") or tx.currency_code or "ZAR"
        tx.currency_code = currency

        # Available to all
        available_to_all_val = request.form.get("available_to_all")
        tx.available_to_all = True if available_to_all_val == "1" else False

        # If available_to_all is checked, agent_id MUST be NULL
        if tx.available_to_all:
            tx.agent_id = None
        else:
            agent_val = request.form.get("agent_id")
            tx.agent_id = int(agent_val) if agent_val not in (None, "", "None") else None

        # Branch selection
        branch_val = request.form.get("branch_id")
        tx.branch_id = int(branch_val) if branch_val not in (None, "", "None") else None

        # Status
        tx.status = request.form.get("status") or tx.status or "pending"

        # Recalculate USD amount with latest rate
        exchange_rate = ExchangeRate.query.filter_by(
            from_currency=currency,
            to_currency="ZAR"
        ).order_by(ExchangeRate.updated_at.desc()).first()

        rate = float(exchange_rate.rate) if exchange_rate else 1.0
        tx.amount_foreign = round(tx.amount_local / rate, 6) if rate else tx.amount_local

        db.session.commit()
        flash("Transaction updated successfully", "success")
        return redirect(url_for("admin.transactions"))


@admin_bp.route("/transactions/<string:txid>/delete", methods=["POST"])
@require_role("admin")
def delete_transaction(txid):
    tx = Transaction.query.filter_by(transaction_id=txid).first()
    if tx:
        db.session.delete(tx)
        db.session.commit()
        flash("Transaction deleted", "success")
    else:
        flash("Transaction not found", "warning")

    return redirect(url_for("admin.transactions"))


# users & agents management
@admin_bp.route("/users")
@require_role("admin")
def users():
    users = User.query.order_by(User.id.desc()).all()
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
    agents_data = db.session.query(
        User.id,
        User.username,
        User.full_name,
        Agent.branch_id
    ).outerjoin(
        Agent, User.id == Agent.user_id
    ).filter(
        User.role == 'agent'
    ).order_by(User.id.desc()).all()

    branches = Branch.query.all()
    return render_template("admin/agents.html", agents=agents_data, branches=branches)


@admin_bp.route("/agents/create", methods=["GET", "POST"])
@require_role("admin")
def create_agent():
    if request.method == "POST":
        full_name = request.form.get("full_name")
        username = request.form.get("username")
        password = request.form.get("password")
        branch_id = request.form.get("branch_id") or None

        try:
            # Create user
            user = User(
                full_name=full_name,
                username=username,
                password=password,  # In production, hash this!
                role="agent",
                branch_id=branch_id,
                created_at=datetime.utcnow()
            )
            db.session.add(user)
            db.session.flush()  # Get user.id

            # Create agent record
            agent = Agent(
                user_id=user.id,
                branch_id=branch_id
            )
            db.session.add(agent)

            db.session.commit()
            flash("Agent created", "success")
            return redirect(url_for("admin.agents"))

        except Exception as e:
            db.session.rollback()
            flash(f"Error creating agent: {str(e)}", "danger")
            return redirect(url_for("admin.agents"))

    branches = Branch.query.all()
    return render_template("admin/create_agent.html", branches=branches)


# branch CRUD
@admin_bp.route("/branches")
@require_role("admin")
def branches():
    branches = Branch.query.order_by(Branch.id.desc()).all()
    return render_template("admin/branches.html", branches=branches)


@admin_bp.route("/branches/create", methods=["GET", "POST"])
@require_role("admin")
def create_branch():
    if request.method == "POST":
        name = request.form.get("name")
        location = request.form.get("location")
        rate_override = request.form.get("rate_override") or None

        branch = Branch(
            name=name,
            location=location,
            rate_override=float(rate_override) if rate_override else None
        )

        db.session.add(branch)
        db.session.commit()
        flash("Branch created", "success")
        return redirect(url_for("admin.branches"))

    # GET request - get latest exchange rate for reference
    latest_rate = ExchangeRate.query.order_by(ExchangeRate.updated_at.desc()).first()
    return render_template("admin/edit_branch.html", branch=None, latest_rate=latest_rate)


@admin_bp.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
@require_role("admin")
def edit_branch(branch_id):
    branch = Branch.query.get_or_404(branch_id)

    if request.method == "POST":
        branch.name = request.form.get("name")
        branch.location = request.form.get("location")
        rate_override = request.form.get("rate_override")
        branch.rate_override = float(rate_override) if rate_override else None

        db.session.commit()
        flash("Branch updated", "success")
        return redirect(url_for("admin.branches"))

    # GET request
    tx_count = Transaction.query.filter_by(branch_id=branch_id).count()
    latest_rate = ExchangeRate.query.order_by(ExchangeRate.updated_at.desc()).first()

    return render_template("admin/edit_branch.html",
                           branch=branch,
                           branch_transaction_count=tx_count,
                           latest_rate=latest_rate)


@admin_bp.route("/branches/<int:branch_id>/delete", methods=["POST"])
@require_role("admin")
def delete_branch(branch_id):
    branch = Branch.query.get(branch_id)
    if branch:
        db.session.delete(branch)
        db.session.commit()
        flash("Branch deleted", "success")
    else:
        flash("Branch not found", "warning")

    return redirect(url_for("admin.branches"))


# logs & reports
@admin_bp.route("/logs")
@require_role("admin")
def logs():
    logs = Log.query.order_by(Log.id.desc()).limit(500).all()
    return render_template("admin/logs.html", logs=logs)


# Edit user (GET shows form, POST saves)
@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@require_role("admin")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        user.full_name = request.form.get("full_name")
        user.username = request.form.get("username")
        user.role = request.form.get("role")
        branch_id = request.form.get("branch_id")
        user.branch_id = int(branch_id) if branch_id else None

        db.session.commit()
        flash("User updated", "success")
        return redirect(url_for("admin.users"))

    # GET
    branches = Branch.query.all()
    return render_template("admin/edit_user.html", user=user, branches=branches)


# Delete user
@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@require_role("admin")
def delete_user(user_id):
    user = User.query.get(user_id)
    if user:
        # Delete associated agent record if exists
        Agent.query.filter_by(user_id=user_id).delete()

        # Delete user
        db.session.delete(user)
        db.session.commit()
        flash("User deleted", "info")
    else:
        flash("User not found", "warning")

    return redirect(url_for("admin.users"))


# route: show breakdown (POST from form -> preview)
@admin_bp.route("/transactions/quote", methods=["POST"])
@require_role("admin")
def transaction_quote():
    # read amount safely
    raw_amount = request.form.get("amount_local") or request.form.get("amount") or "0"
    try:
        amount_local = float(raw_amount)
    except ValueError:
        flash("Invalid amount", "warning")
        return redirect(url_for("admin.transactions"))

    currency = request.form.get("currency_code") or "ZAR"

    # fees
    pct = float(current_app.config.get("FEE_PERCENT", 0.01))
    flat = float(current_app.config.get("FEE_FLAT", 10.0))
    fee_percent = round(amount_local * pct, 2)
    subtotal = round(amount_local + fee_percent + flat, 2)

    # get latest rate for currency -> ZAR (fallback 1)
    exchange_rate = ExchangeRate.query.filter_by(
        from_currency=currency,
        to_currency="ZAR"
    ).order_by(ExchangeRate.updated_at.desc()).first()

    rate = float(exchange_rate.rate) if exchange_rate else 1.0
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
    # Daily stats
    daily = db.session.query(
        cast(Transaction.timestamp, Date).label('day'),
        func.count().label('ct'),
        func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
    ).group_by(
        cast(Transaction.timestamp, Date)
    ).order_by(text('day DESC')).limit(30).all()

    # Monthly stats
    monthly = db.session.query(
        func.to_char(Transaction.timestamp, 'YYYY-MM').label('month'),
        func.count().label('ct'),
        func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
    ).group_by(
        func.to_char(Transaction.timestamp, 'YYYY-MM')
    ).order_by(text('month DESC')).limit(24).all()

    # Yearly stats
    yearly = db.session.query(
        func.to_char(Transaction.timestamp, 'YYYY').label('year'),
        func.count().label('ct'),
        func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
    ).group_by(
        func.to_char(Transaction.timestamp, 'YYYY')
    ).order_by(text('year DESC')).all()

    return render_template("admin/reports.html", daily=daily, monthly=monthly, yearly=yearly)


@admin_bp.route("/reports/daily")
@require_role("admin")
def reports_daily():
    # Get selected date from query parameter
    selected_date = request.args.get('date')

    if selected_date:
        # Today's summary for selected date
        today_result = db.session.query(
            func.count().label('count'),
            func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
        ).filter(
            cast(Transaction.timestamp, Date) == selected_date
        ).first()

        # Get transactions for selected date
        transactions = db.session.query(
            Transaction,
            User.full_name.label('agent_name')
        ).outerjoin(
            User, Transaction.agent_id == User.id
        ).filter(
            cast(Transaction.timestamp, Date) == selected_date
        ).order_by(Transaction.timestamp.desc()).all()
    else:
        # Today's summary
        today = datetime.utcnow().date()
        today_result = db.session.query(
            func.count().label('count'),
            func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
        ).filter(
            cast(Transaction.timestamp, Date) == today
        ).first()

        # Get transactions for today
        transactions = db.session.query(
            Transaction,
            User.full_name.label('agent_name')
        ).outerjoin(
            User, Transaction.agent_id == User.id
        ).filter(
            cast(Transaction.timestamp, Date) == today
        ).order_by(Transaction.timestamp.desc()).all()

    # Get last 7 days summary for chart
    seven_days_ago = datetime.utcnow().date() - timedelta(days=7)
    daily_summary = db.session.query(
        cast(Transaction.timestamp, Date).label('day'),
        func.count().label('count'),
        func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
    ).filter(
        cast(Transaction.timestamp, Date) >= seven_days_ago
    ).group_by(
        cast(Transaction.timestamp, Date)
    ).order_by(text('day DESC')).all()

    return render_template("admin/reports_daily.html",
                           today=today_result,
                           rows=transactions,
                           daily_summary=daily_summary,
                           selected_date=selected_date)


@admin_bp.route("/reports/monthly")
@require_role("admin")
def reports_monthly():
    selected_year = request.args.get('year')
    selected_month = request.args.get('month')

    query = db.session.query(
        func.to_char(Transaction.timestamp, 'YYYY-MM').label('month'),
        func.count().label('count'),
        func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
    )

    if selected_year:
        query = query.filter(func.to_char(Transaction.timestamp, 'YYYY') == selected_year)

        if selected_month:
            query = query.filter(func.to_char(Transaction.timestamp, 'MM') == selected_month)
    else:
        # Default to last 12 months
        one_year_ago = datetime.utcnow() - timedelta(days=365)
        query = query.filter(Transaction.timestamp >= one_year_ago)

    rows = query.group_by(
        func.to_char(Transaction.timestamp, 'YYYY-MM')
    ).order_by(text('month DESC')).all()

    # Get available years for filter dropdown
    available_years_result = db.session.query(
        func.distinct(func.to_char(Transaction.timestamp, 'YYYY')).label('year')
    ).order_by(text('year DESC')).all()
    available_years = [row.year for row in available_years_result]

    return render_template("admin/reports_monthly.html",
                           rows=rows,
                           selected_year=selected_year,
                           selected_month=selected_month,
                           available_years=available_years)


@admin_bp.route("/reports/yearly")
@require_role("admin")
def reports_yearly():
    start_year = request.args.get('start_year')
    end_year = request.args.get('end_year')

    query = db.session.query(
        func.to_char(Transaction.timestamp, 'YYYY').label('year'),
        func.count().label('count'),
        func.coalesce(func.sum(Transaction.amount_local), 0).label('total')
    )

    if start_year:
        query = query.filter(func.to_char(Transaction.timestamp, 'YYYY') >= start_year)

        if end_year:
            query = query.filter(func.to_char(Transaction.timestamp, 'YYYY') <= end_year)

    rows = query.group_by(
        func.to_char(Transaction.timestamp, 'YYYY')
    ).order_by(text('year DESC')).all()

    # Get available years for filter dropdown
    available_years_result = db.session.query(
        func.distinct(func.to_char(Transaction.timestamp, 'YYYY')).label('year')
    ).order_by(text('year DESC')).all()
    available_years = [row.year for row in available_years_result]

    return render_template("admin/reports_yearly.html",
                           rows=rows,
                           start_year=start_year,
                           end_year=end_year,
                           available_years=available_years)


@admin_bp.route("/rates", methods=["GET", "POST"])
@require_role("admin")
def rates():
    if request.method == "POST":
        if "set_rate" in request.form:
            try:
                rate = float(request.form.get("rate"))
                source = request.form.get("source") or "Manual"

                exchange_rate = ExchangeRate(
                    from_currency="USD",
                    to_currency="ZAR",
                    rate=rate,
                    source=source,
                    updated_at=datetime.utcnow()
                )
                db.session.add(exchange_rate)
                db.session.commit()
                flash(f"Manual rate saved: USD->ZAR = {rate}", "success")
            except ValueError:
                flash("Invalid rate", "danger")

        elif "toggle_auto" in request.form:
            current = get_setting("auto_update_rates") or "true"
            newv = "false" if current.lower() == "true" else "true"
            set_setting("auto_update_rates", newv)
            flash(f"Auto-update {'enabled' if newv == 'true' else 'disabled'}", "info")

        elif "fetch_now" in request.form:
            res = update_usd_zar()
            if res.get("ok"):
                flash(f"Rates updated: USD->ZAR = {res['rate']} (Source: {res.get('source', 'unknown')})", "success")
            else:
                flash(f"Could not fetch rates: {res.get('error')}", "danger")

        elif "clear_history" in request.form:
            # Keep only the latest rate
            latest = ExchangeRate.query.filter_by(
                from_currency='USD',
                to_currency='ZAR'
            ).order_by(ExchangeRate.updated_at.desc()).first()

            if latest:
                # Delete all except the latest
                ExchangeRate.query.filter(
                    and_(
                        ExchangeRate.from_currency == 'USD',
                        ExchangeRate.to_currency == 'ZAR',
                        ExchangeRate.id != latest.id
                    )
                ).delete(synchronize_session=False)
                db.session.commit()
                flash("Rate history cleared, keeping only latest rate", "info")
            else:
                flash("No rates to clear", "warning")

        return redirect(url_for("admin.rates"))

    # GET request
    latest = get_latest_rate()
    auto_setting = get_setting("auto_update_rates") or "true"
    auto = auto_setting == "true"

    # Get rate history for chart
    history = ExchangeRate.query.filter_by(
        from_currency='USD',
        to_currency='ZAR'
    ).order_by(ExchangeRate.updated_at.desc()).limit(50).all()

    # Get auto-update status
    from .rates import should_update_rates
    needs_update = should_update_rates()

    return render_template("admin/rates.html",
                           latest=latest,
                           auto=auto,
                           history=history,
                           needs_update=needs_update)


@admin_bp.route("/rates/fetch_now", methods=["POST"])
@require_role("admin")
def rates_fetch_now():
    """Separate endpoint for fetching rates via AJAX or direct POST"""
    res = update_usd_zar()
    if res.get("ok"):
        flash(f"Rates updated: USD->ZAR = {res['rate']} (Source: {res.get('source', 'unknown')})", "success")
    else:
        flash(f"Could not fetch rates: {res.get('error')}", "danger")
    return redirect(url_for("admin.rates"))


@admin_bp.route("/agents/<int:agent_id>/delete", methods=["POST"])
@require_role("admin")
def delete_agent(agent_id):
    try:
        # First delete from agents table (if exists)
        Agent.query.filter_by(user_id=agent_id).delete()

        # Then delete from users table
        user = User.query.get(agent_id)
        if user:
            db.session.delete(user)

        db.session.commit()
        flash("Agent deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting agent: {str(e)}", "danger")

    return redirect(url_for("admin.agents"))


@admin_bp.route("/transactions/<txid>")
@require_role("admin")
def view_transaction(txid):
    """View transaction details including who completed it"""

    from sqlalchemy.orm import aliased

    # Create aliases for multiple joins to the same table
    agent_user = aliased(User, name='agent_user')
    completed_by_user = aliased(User, name='completed_by_user')
    created_by_user = aliased(User, name='created_by_user')
    completer_user = aliased(User, name='completer_user')
    verifier_user = aliased(User, name='verifier_user')
    picker_user = aliased(User, name='picker_user')
    creator_user = aliased(User, name='creator_user')

    # Query with all necessary joins - FIXED VERSION
    query = (Transaction.query
             .outerjoin(agent_user, Transaction.agent_id == agent_user.id)
             .outerjoin(completed_by_user, Transaction.completed_by == completed_by_user.id)
             .outerjoin(created_by_user, Transaction.created_by == created_by_user.id)
             .outerjoin(completer_user, Transaction.completed_by == completer_user.id)
             .outerjoin(verifier_user, Transaction.verified_by == verifier_user.id)
             .outerjoin(picker_user, Transaction.picked_by == picker_user.id)
             .outerjoin(creator_user, Transaction.created_by == creator_user.id)
             .outerjoin(Branch, Transaction.branch_id == Branch.id)
             .add_columns(
        # Basic info columns
        agent_user.full_name.label('agent_name'),
        agent_user.username.label('agent_username'),
        completed_by_user.full_name.label('completed_by_name'),
        completed_by_user.username.label('completed_by_username'),
        created_by_user.full_name.label('created_by_name'),
        Branch.name.label('branch_name'),
        # Add the actual relationship objects
        agent_user,  # This gives you the User object, not a label
        completer_user,  # Same here
        verifier_user,  # Same here
        picker_user,  # Same here
        creator_user,  # Same here
        Branch  # This gives you the Branch object
    )
             .filter(Transaction.transaction_id == txid))

    result = query.first()

    if not result:
        flash("Transaction not found", "error")
        return redirect(url_for("admin.transactions"))

    # Unpack the result - FIXED: Now includes actual objects, not labels
    (transaction, agent_name, agent_username,
     completed_by_name, completed_by_username,
     created_by_name, branch_name,
     agent_obj, completer_obj, verifier_obj, picker_obj, creator_obj, branch_obj) = result

    # DEBUG
    print(f"✅ Transaction loaded: {transaction.transaction_id}")
    print(f"   Agent object: {agent_obj}")
    print(f"   Branch object: {branch_obj}")

    # Get activity logs
    logs = []  # Replace with: ActivityLog.query.filter_by(transaction_id=transaction.id).all()

    # Pass to template - your template uses 'tx' for the transaction
    return render_template(
        "admin/view_transactions.html",
        tx=transaction,  # Main transaction object
        agent_name=agent_name,
        agent_username=agent_username,
        completed_by_name=completed_by_name,
        completed_by_username=completed_by_username,
        created_by_name=created_by_name,
        branch_name=branch_name,
        # Pass the actual objects for template relationships
        agent=agent_obj,
        completer=completer_obj,
        verifier=verifier_obj,
        picker=picker_obj,
        creator=creator_obj,
        branch=branch_obj,
        logs=logs
    )
@admin_bp.route("/transactions/<txid>/verify", methods=["POST"])
@require_role("admin")
def verify_transaction(txid):
    """Admin verifies a completed transaction"""
    admin_id = session.get("user_id")

    try:
        # Get admin name
        admin = User.query.get(admin_id)
        admin_name = admin.full_name if admin else f"Admin {admin_id}"

        # Update transaction with verification info
        tx = Transaction.query.filter_by(
            transaction_id=txid,
            status='completed'
        ).first()

        if not tx:
            flash("Transaction not found or not completed", "warning")
            return redirect(url_for("admin.transactions"))

        tx.verified_by = admin_id
        tx.verified_at = datetime.utcnow()

        # Log the verification
        log = Log(
            user_id=admin_id,
            action="verified_tx",
            details=f"{txid} verified by {admin_name}"
        )
        db.session.add(log)

        db.session.commit()
        flash(f"Transaction {txid} verified by {admin_name}", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Error verifying transaction: {str(e)}", "danger")

    return redirect(url_for("admin.view_transaction", txid=txid))


@admin_bp.route("/transactions/<txid>/complete", methods=["POST"])
@require_role("admin")
def mark_completed(txid):
    """Admin marks a transaction as completed"""
    admin_id = session.get("user_id")

    try:
        # Get admin name
        admin = User.query.get(admin_id)
        admin_name = admin.full_name if admin else f"Admin {admin_id}"

        # Update transaction
        tx = Transaction.query.filter_by(transaction_id=txid).first()
        if not tx:
            flash("Transaction not found", "warning")
            return redirect(url_for("admin.transactions"))

        tx.status = 'completed'
        tx.completed_by = admin_id
        tx.completed_at = datetime.utcnow()
        tx.timestamp = datetime.utcnow()

        # Log
        log = Log(
            user_id=admin_id,
            action="admin_completed_tx",
            details=f"{txid} marked completed by admin {admin_name}"
        )
        db.session.add(log)

        db.session.commit()
        flash(f"Transaction {txid} marked as completed by admin", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for("admin.view_transaction", txid=txid))


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


@admin_bp.route('/api/dashboard-balance')
def dashboard_balance():
    """Fast endpoint specifically for dashboard balance updates"""
    try:
        # DIRECT QUERY - Same as your manage_dollar page
        balance_data = DollarBalance.query.order_by(DollarBalance.id.desc()).first()

        if balance_data:
            return jsonify({
                'success': True,
                'balance': float(balance_data.current_balance),
                'last_updated': balance_data.last_updated.isoformat() if balance_data.last_updated else None,
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


@admin_bp.route("/api/dollar_balance")
@require_role("admin")
def get_dollar_balance():
    """API endpoint to get current USD balance"""
    try:
        balance = DollarBalance.query.first()

        if balance:
            return jsonify({
                'balance': float(balance.current_balance),
                'last_updated': balance.last_updated.isoformat() if balance.last_updated else None,
                'currency': 'USD'
            })
        else:
            return jsonify({'error': 'Balance not found'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Note: DollarBalanceLog routes are removed since the model doesn't exist
# If you need them, you'll need to create the DollarBalanceLog model first

@admin_bp.route("/dollar_balance/manage", methods=["GET", "POST"])
@require_role("admin")
def manage_dollar_balance():
    """Page to manually adjust dollar balance"""
    if request.method == "POST":
        action = request.form.get("action")
        amount = float(request.form.get("amount", 0))
        notes = request.form.get("notes", "")

        if amount <= 0:
            flash("Amount must be greater than 0", "warning")
            return redirect(url_for("admin.manage_dollar_balance"))

        try:
            # Get current balance
            balance = DollarBalance.query.first()
            if not balance:
                balance = DollarBalance(current_balance=0.0)
                db.session.add(balance)

            current_balance = float(balance.current_balance)

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
            balance.current_balance = new_balance
            balance.last_updated = datetime.utcnow()

            # Note: Would need DollarBalanceLog model to log this
            # For now, create a regular log
            log = Log(
                user_id=session.get("user_id"),
                action="balance_adjustment",
                details=f"{description}. Balance: ${current_balance:.2f} → ${new_balance:.2f}"
            )
            db.session.add(log)

            db.session.commit()
            flash(f"✅ Balance updated successfully! ${current_balance:.2f} → ${new_balance:.2f}", "success")

        except Exception as e:
            db.session.rollback()
            flash(f"Error updating balance: {str(e)}", "danger")

        return redirect(url_for("admin.manage_dollar_balance"))

    # GET request - show current balance and recent logs
    balance = DollarBalance.query.first()

    # Get recent manual adjustment logs
    recent_logs = Log.query.filter(
        Log.action.in_(['balance_adjustment', 'balance_update_error'])
    ).order_by(Log.id.desc()).limit(10).all()

    return render_template("admin/manage_dollar_balance.html",
                           balance=balance,
                           recent_logs=recent_logs)