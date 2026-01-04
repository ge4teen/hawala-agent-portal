import boto3
import json
import logging
import uuid
from flask import current_app
from datetime import datetime

logger = logging.getLogger(__name__)


class SNSClient:
    def __init__(self, app=None):
        self.sns = None
        self.topic_arn = None
        self.is_fifo = False

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize SNS client with Flask app"""
        try:
            self.sns = boto3.client(
                'sns',
                region_name=app.config.get('AWS_REGION', 'af-south-1'),
                aws_access_key_id=app.config.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=app.config.get('AWS_SECRET_ACCESS_KEY')
            )

            self.topic_arn = app.config.get('AWS_SNS_TOPIC_ARN')
            self.is_fifo = '.fifo' in (self.topic_arn or '')

            # Test connection
            if self.topic_arn:
                response = self.sns.get_topic_attributes(TopicArn=self.topic_arn)
                logger.info(f"✅ SNS connected to {'FIFO' if self.is_fifo else 'Standard'} topic: {self.topic_arn}")

        except Exception as e:
            logger.error(f"❌ Failed to initialize SNS: {e}")

    def send_notification(self, message, subject=None, message_group_id=None):
        """
        Send notification to SNS topic
        For FIFO topics: message_group_id is REQUIRED
        """
        if not self.sns or not self.topic_arn:
            logger.warning("SNS client not initialized")
            return None

        try:
            # Prepare message
            if isinstance(message, dict):
                message = json.dumps(message, default=str)

            # Prepare publish parameters
            publish_kwargs = {
                'TopicArn': self.topic_arn,
                'Message': message
            }

            # FIFO topics require MessageGroupId and MessageDeduplicationId
            if self.is_fifo:
                if not message_group_id:
                    # Default to transaction-based grouping
                    message_group_id = 'transactions'

                publish_kwargs['MessageGroupId'] = message_group_id
                publish_kwargs['MessageDeduplicationId'] = str(uuid.uuid4())

            if subject and not self.is_fifo:  # FIFO topics don't support Subject
                publish_kwargs['Subject'] = subject

            # Send message
            response = self.sns.publish(**publish_kwargs)
            logger.info(f"📨 Notification sent: {response['MessageId']}")
            return response['MessageId']

        except Exception as e:
            logger.error(f"❌ Failed to send notification: {e}")
            return None

    def send_transaction_notification(self, transaction, action, agent_id=None):
        """
        Send transaction notification with proper FIFO grouping

        Args:
            transaction: Transaction object
            action: Notification action type
            agent_id: Optional agent ID for agent-specific notifications
        """
        # Updated notification types dictionary
        notification_types = {
            'created': {
                'title': 'New Transaction Created',
                'message': 'New transaction #{id} for ZAR {amount}'
            },
            'created_available': {
                'title': 'New Transaction Available',
                'message': 'New transaction #{id} for ZAR {amount} is available to all agents'
            },
            'created_assigned': {
                'title': 'New Transaction Assigned',
                'message': 'New transaction #{id} for ZAR {amount} has been assigned to an agent'
            },
            'completed': {
                'title': 'Transaction Completed',
                'message': 'Transaction #{id} has been completed'
            },
            'verified': {
                'title': 'Transaction Verified',
                'message': 'Transaction #{id} has been verified'
            },
            'picked': {
                'title': 'Transaction Picked',
                'message': 'Transaction #{id} was picked by an agent'
            },
            'assigned': {
                'title': 'Transaction Assigned',
                'message': 'Transaction #{id} has been assigned to agent'
            },
            'low_balance': {
                'title': 'Low Dollar Balance Alert',
                'message': 'System dollar balance is ${balance} - Please add funds'
            },
            'balance_updated': {
                'title': 'Balance Updated',
                'message': 'System dollar balance updated to ${balance}'
            },
            'sms_sent': {
                'title': 'SMS Notification Sent',
                'message': 'SMS sent for transaction #{id}'
            }
        }

        # Get notification template
        template = notification_types.get(action, {
            'title': f'Transaction {action.title()}',
            'message': f'Transaction #{getattr(transaction, "transaction_id", "N/A")} - {action}'
        })

        # Format message text
        message_text = template['message'].format(
            id=getattr(transaction, 'transaction_id', 'N/A'),
            amount=getattr(transaction, 'amount_local', 0),
            balance=getattr(transaction, 'current_balance', 0)
        )

        # Create structured message data
        message_data = {
            'type': 'transaction_notification',
            'action': action,
            'title': template['title'],
            'message': message_text,
            'transaction_id': getattr(transaction, 'transaction_id', None),
            'amount_local': getattr(transaction, 'amount_local', 0),
            'amount_foreign': getattr(transaction, 'amount_foreign', 0),
            'currency_code': getattr(transaction, 'currency_code', 'ZAR'),
            'status': getattr(transaction, 'status', 'unknown'),
            'sender': getattr(transaction, 'sender_name', ''),
            'receiver': getattr(transaction, 'receiver_name', ''),
            'agent_id': agent_id or getattr(transaction, 'agent_id', None),
            'available_to_all': getattr(transaction, 'available_to_all', False),
            'created_by': getattr(transaction, 'created_by', None),
            'timestamp': datetime.utcnow().isoformat(),
            'source': 'hawala_system',
            'version': '1.0'
        }

        # Determine message group for FIFO ordering
        message_group_id = 'transactions'  # Default group

        if action == 'low_balance' or action == 'balance_updated':
            message_group_id = 'system_alerts'
        elif agent_id:
            message_group_id = f'agent_{agent_id}'
        elif action == 'created_available':
            message_group_id = 'available_transactions'
        elif action == 'created_assigned':
            # If transaction has agent_id, use agent grouping
            if getattr(transaction, 'agent_id', None):
                message_group_id = f'agent_{transaction.agent_id}'
            else:
                message_group_id = 'admin_transactions'

        # Send notification
        return self.send_notification(
            message=message_data,
            subject=template['title'] if not self.is_fifo else None,
            message_group_id=message_group_id
        )

    def send_simple_notification(self, title, message, notification_type='system', data=None):
        """
        Send a simple notification without requiring a transaction object

        Args:
            title: Notification title
            message: Notification message
            notification_type: Type of notification (system, alert, info)
            data: Additional data dictionary
        """
        message_data = {
            'type': notification_type,
            'title': title,
            'message': message,
            'timestamp': datetime.utcnow().isoformat(),
            'source': 'hawala_system'
        }

        if data and isinstance(data, dict):
            message_data.update(data)

        # Determine message group
        message_group_id = 'system_alerts' if notification_type == 'alert' else 'system_info'

        return self.send_notification(
            message=message_data,
            subject=title if not self.is_fifo else None,
            message_group_id=message_group_id
        )


# Global instance
sns_client = SNSClient()
global sns_client