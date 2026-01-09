import requests
import json
import logging
from flask import current_app

logger = logging.getLogger(__name__)


from twilio_sms_service import TwilioSMSService

service = TwilioSMSService()

def send_sms(to_number: str, message: str):
    """
    Signature unchanged. Drop-in replacement for ClickSend.
    """
    return service.send_sms(to_number, message)

def clean_phone_number(phone: str) -> str:
    """
    Clean and format phone number for ClickSend

    Args:
        phone: Raw phone number

    Returns:
        str: Cleaned phone number with country code
    """
    if not phone:
        return ""

    # Remove all non-digit characters
    cleaned = ''.join(filter(str.isdigit, phone))

    # Handle South African numbers
    if cleaned.startswith('0'):
        cleaned = '27' + cleaned[1:]  # Replace leading 0 with 27
    elif not cleaned.startswith('27') and len(cleaned) == 9:
        cleaned = '27' + cleaned  # Add 27 prefix for 9-digit numbers

    # Add + prefix for international format
    if cleaned and not cleaned.startswith('+'):
        cleaned = '+' + cleaned

    return cleaned


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
    """
    Build SMS message template for transaction notifications

    Args:
        txid: Transaction ID
        agent: Agent name/ID
        sender_name: Sender's name
        sender_phone: Sender's phone
        receiver_name: Receiver's name
        receiver_phone: Receiver's phone
        amount: Transaction amount
        status: Transaction status

    Returns:
        str: Formatted SMS message
    """
    # Clean phone numbers for display
    sender_display = clean_phone_number(sender_phone) if sender_phone else "N/A"
    receiver_display = clean_phone_number(receiver_phone) if receiver_phone else "N/A"

    # Format amount
    try:
        amount_formatted = f"ZAR {float(amount):,.2f}"
    except (ValueError, TypeError):
        amount_formatted = f"ZAR {amount}"

    # Build message with character limit consideration (SMS max 160 chars per segment)
    message = (
        f"ISA Hawala: TX#{txid} assigned to Agent {agent}. "
        f"From: {sender_name} ({sender_display}) "
        f"To: {receiver_name} ({receiver_display}) "
        f"Amount: {amount_formatted}. Status: {status}."
    )

    # Truncate if too long (should be rare)
    if len(message) > 160:
        message = message[:157] + "..."

    return message


def send_transaction_sms_notification(transaction, agent_name=None):
    """
    Send SMS notification for a transaction and log result

    Args:
        transaction: Transaction object
        agent_name: Optional agent name for display

    Returns:
        dict: SMS sending result
    """


    if not transaction.receiver_phone:
        return {"success": False, "error": "No receiver phone number"}

    # Get agent name if not provided
    if not agent_name and transaction.agent_id:
        from models import User
        agent = User.query.get(transaction.agent_id)
        agent_name = agent.full_name if agent else f"Agent #{transaction.agent_id}"

    # Build SMS message
    sms_message = build_sms_template(
        txid=transaction.transaction_id,
        agent=agent_name or "Unassigned",
        sender_name=transaction.sender_name,
        sender_phone=transaction.sender_phone,
        receiver_name=transaction.receiver_name,
        receiver_phone=transaction.receiver_phone,
        amount=transaction.amount_local,
        status=transaction.status.capitalize()
    )

    # Send SMS
    sms_result = send_sms(transaction.receiver_phone, sms_message)

    # Send SNS notification about SMS
    try:
        if sms_result.get("success"):
            # Send success notification to SNS
            sms_notification_id = sns_client.send_transaction_notification(
                transaction=transaction,
                action='sms_sent_success',
                agent_id=transaction.agent_id,
                sms_result=sms_result
            )
            logger.info(f"✅ SMS success SNS notification: {sms_notification_id}")
        else:
            # Send failure notification to SNS
            sms_notification_id = sns_client.send_transaction_notification(
                transaction=transaction,
                action='sms_sent_failed',
                agent_id=transaction.agent_id,
                sms_result=sms_result
            )
            logger.warning(f"⚠️ SMS failure SNS notification: {sms_notification_id}")
    except Exception as sns_error:
        logger.error(f"❌ Failed to send SMS SNS notification: {sns_error}")

    return sms_result