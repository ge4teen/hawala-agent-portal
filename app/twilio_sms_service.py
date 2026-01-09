# twilio_sms_service.py

import os
from twilio.rest import Client

class TwilioSMSService:
    """Emergency Twilio SMS replacement"""

    def __init__(self):
        self.client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN")
        )
        self.sms_number = os.getenv("TWILIO_SMS_NUMBER")

    def send_sms(self, to_number: str, message: str) -> dict:
        """Drop-in replacement for old send_sms"""
        try:
            # Normalize South African numbers to E.164
            to_number = self.normalize_number(to_number)

            msg = self.client.messages.create(
                body=message,
                from_=self.sms_number,
                to=to_number
            )
            return {
                "success": True,
                "message_id": msg.sid,
                "status": msg.status,
                "raw_response": {"sid": msg.sid}
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "raw_response": {}
            }

    @staticmethod
    def normalize_number(number: str) -> str:
        number = number.strip().replace(" ", "")
        if number.startswith("0"):
            return "+27" + number[1:]
        if number.startswith("27"):
            return "+" + number
        if number.startswith("+"):
            return number
        raise ValueError(f"Invalid phone number format: {number}")
