import boto3
import json

# Your configuration
config = {
    'AWS_ACCESS_KEY_ID': 'AKIAYKDSWKE2UZ52PMHY',
    'AWS_SECRET_ACCESS_KEY': 'hQdxNFfRNzKfSJkAwpfRlgYqKujVQh7WAiSz0COM',
    'AWS_REGION': 'af-south-1',
    'AWS_SNS_TOPIC_ARN': 'arn:aws:sns:af-south-1:571471188277:hawala-transactions.fifo'
}

print("🧪 Testing SNS FIFO Setup...")
print(f"Region: {config['AWS_REGION']}")
print(f"Topic ARN: {config['AWS_SNS_TOPIC_ARN']}")

try:
    # Initialize SNS client
    sns = boto3.client(
        'sns',
        region_name=config['AWS_REGION'],
        aws_access_key_id=config['AWS_ACCESS_KEY_ID'],
        aws_secret_access_key=config['AWS_SECRET_ACCESS_KEY']
    )

    # Get topic attributes
    response = sns.get_topic_attributes(TopicArn=config['AWS_SNS_TOPIC_ARN'])
    print(f"✅ Topic found: {response['Attributes']['DisplayName']}")
    print(f"   FIFO: {response['Attributes']['FifoTopic']}")

    # Test sending a message (FIFO requires MessageGroupId and MessageDeduplicationId)
    import uuid

    test_message = {
        'type': 'test',
        'message': 'Test notification from Hawala system',
        'transaction_id': 'TEST-001',
        'timestamp': '2024-01-01T12:00:00Z'
    }

    response = sns.publish(
        TopicArn=config['AWS_SNS_TOPIC_ARN'],
        Message=json.dumps(test_message),
        MessageGroupId='test_group',
        MessageDeduplicationId=str(uuid.uuid4())
    )

    print(f"✅ Test message sent successfully!")
    print(f"   Message ID: {response['MessageId']}")
    print(f"   Sequence Number: {response.get('SequenceNumber', 'N/A')}")

    # List subscriptions
    print("\n📋 Checking subscriptions...")
    subs_response = sns.list_subscriptions_by_topic(TopicArn=config['AWS_SNS_TOPIC_ARN'])

    if subs_response['Subscriptions']:
        print(f"   Found {len(subs_response['Subscriptions'])} subscription(s):")
        for sub in subs_response['Subscriptions']:
            print(f"   - {sub['Protocol']}: {sub['Endpoint']} ({sub['SubscriptionArn']})")
    else:
        print("   No subscriptions yet")
        print("\n💡 Tip: Add email/SMS subscriptions in AWS Console:")
        print("   1. Go to SNS → Topics → hawala-transactions.fifo")
        print("   2. Click 'Create subscription'")
        print("   3. Choose protocol (email, sms, etc.)")
        print("   4. Enter endpoint and create")

except Exception as e:
    print(f"❌ Error: {e}")
    print("\n🔧 Troubleshooting:")
    print("1. Check if credentials are correct")
    print("2. Verify the user has SNS permissions")
    print("3. Make sure the topic exists in af-south-1 region")