import requests, json
from flask import current_app

def send_sms(to_number: str, message: str):
    username = current_app.config.get("CLICKSEND_USERNAME")
    api_key  = current_app.config.get("CLICKSEND_API_KEY")
    if not username or not api_key:
        return {"error": "ClickSend credentials not configured"}
    url = "https://rest.clicksend.com/v3/sms/send"
    payload = {"messages": [{"source": "python", "body": message, "to": to_number}]}
    try:
        resp = requests.post(url, json=payload, auth=(username, api_key), headers={"Content-Type": "application/json"}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


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
