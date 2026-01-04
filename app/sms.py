import requests
import json
import logging
from flask import current_app

logger = logging.getLogger(__name__)


def send_sms(to_number: str, message: str):
    """
    Send SMS using ClickSend API

    Args:
        to_number: Phone number with country code (e.g., +27731234567)
        message: SMS message content

    Returns:
        dict: Response from ClickSend API or error dict
    """
    username = current_app.config.get("CLICKSEND_USERNAME")
    api_key = current_app.config.get("CLICKSEND_API_KEY")

    if not username or not api_key:
        error_msg = "ClickSend credentials not configured"
        logger.error(error_msg)
        return {"error": error_msg, "success": False}

    # Clean phone number
    to_number = clean_phone_number(to_number)

    if not to_number:
        error_msg = "Invalid phone number format"
        logger.error(error_msg)
        return {"error": error_msg, "success": False}

    url = "https://rest.clicksend.com/v3/sms/send"
    payload = {
        "messages": [{
            "source": "hawala_system",
            "body": message,
            "to": to_number
        }]
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        logger.info(f"📱 Sending SMS to {to_number}: {message[:50]}...")
        resp = requests.post(
            url,
            json=payload,
            auth=(username, api_key),
            headers=headers,
            timeout=15
        )
        resp.raise_for_status()

        result = resp.json()

        if result.get("response_code") == "SUCCESS":
            logger.info(f"✅ SMS sent successfully to {to_number}")
            return {
                "success": True,
                "message_id": result.get("data", {}).get("messages", [{}])[0].get("message_id"),
                "status": result.get("data", {}).get("messages", [{}])[0].get("status"),
                "raw_response": result
            }
        else:
            logger.error(f"❌ SMS failed: {result}")
            return {
                "success": False,
                "error": result.get("response_msg", "Unknown error"),
                "raw_response": result
            }

    except requests.exceptions.Timeout:
        error_msg = "ClickSend API timeout"
        logger.error(error_msg)
        return {"error": error_msg, "success": False}
    except requests.exceptions.RequestException as e:
        error_msg = f"ClickSend API error: {str(e)}"
        logger.error(error_msg)
        return {"error": error_msg, "success": False}
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        return {"error": error_msg, "success": False}


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