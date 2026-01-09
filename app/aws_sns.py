# app/aws_sns.py
import boto3
import os
                            
# Read credentials from environment variables
sns_client = boto3.client(
    "sns",
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    region_name=os.environ.get("AWS_REGION", "us-east-1")
)

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")


def send_sns_notification(txid, action, admin_name, amount=None, agent_id=None):
    message = f"Transaction {txid} {action} by {admin_name}"
    if amount:
        message += f", Amount: {amount}"
    if agent_id:
        message += f", Agent: {agent_id}"

    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject=f"Transaction {action.capitalize()}"
        )
    except Exception as e:
        # Do NOT block your app if SNS fails
        print(f"Failed to send SNS: {e}")
