"""
Razorpay webhook handler
"""
import hmac
import hashlib
import json
import logging
from flask import request, current_app
from ..config import RAZORPAY_WEBHOOK_SECRET

logger = logging.getLogger('payment_gateway')

def verify_razorpay_signature(payload, signature):
    """
    Verify the Razorpay webhook signature using HMAC-SHA256
    
    Args:
        payload: The request body (raw bytes)
        signature: The X-Razorpay-Signature header value
        
    Returns:
        bool: True if the signature is valid, False otherwise
    """
    webhook_secret = RAZORPAY_WEBHOOK_SECRET
    
    if not webhook_secret:
        logger.warning("Razorpay webhook secret not configured")
        return False
        
    expected_signature = hmac.new(
        webhook_secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_signature, signature)


def handle_razorpay_webhook(payment_service):
    """
    Handle Razorpay webhook events and update subscription statuses
    
    Args:
        payment_service: The PaymentService instance
        
    Returns:
        tuple: Response object and status code
    """
    try:
        # Get the webhook signature
        webhook_signature = request.headers.get('X-Razorpay-Signature')
        payload = request.data
        
        logger.info(f"Received Razorpay webhook, payload length: {len(payload)}")
        
        # Verify the signature if provided
        if webhook_signature:
            if not verify_razorpay_signature(payload, webhook_signature):
                logger.warning("Invalid Razorpay webhook signature")
                return {'error': 'Invalid signature'}, 400
        
        # Parse the webhook payload
        webhook_data = request.json
        event_type = webhook_data.get('event')
        
        # Generate event ID for idempotency (Razorpay doesn't provide unique event ID)
        event_id = f"razorpay_{event_type}_{webhook_data.get('created_at', '')}"
        if 'payload' in webhook_data and 'subscription' in webhook_data['payload']:
            sub_data = webhook_data['payload']['subscription']
            sub_id = sub_data.get('entity', {}).get('id') if 'entity' in sub_data else sub_data.get('id')
            if sub_id:
                event_id = f"razorpay_{event_type}_{sub_id}_{webhook_data.get('created_at', '')}"
        
        logger.info(f"Processing Razorpay webhook: {event_type}, Event ID: {event_id}")
        
        # Check idempotency
        if payment_service.db.is_event_processed(event_id, 'razorpay'):
            logger.info(f"Razorpay event {event_id} already processed")
            return {'status': 'already_processed'}, 200
        
        # Enhanced debugging for subscription events
        if event_type and event_type.startswith('subscription.'):
            subscription_data = webhook_data.get('payload', {}).get('subscription', {})
            subscription_id = subscription_data.get('entity', {}).get('id') if 'entity' in subscription_data else subscription_data.get('id')
            
            logger.info(f"Subscription event: {event_type}, ID: {subscription_id}")
            
            # Check for missing data
            if not subscription_id:
                logger.warning(f"Missing subscription ID in {event_type} webhook")
                logger.debug(f"Webhook payload structure: {json.dumps(webhook_data, indent=2)}")
            
            # Reset quota on subscription renewal events
            if event_type == 'subscription.charged':
                # Get the internal subscription ID
                subscription = payment_service.get_subscription_by_gateway_id(subscription_id, 'razorpay')
                
                if subscription:
                    logger.info(f"Resetting quota for subscription {subscription.get('id')} after renewal")
                    payment_service.reset_quota_on_renewal(subscription.get('id'))
        
        # Process the webhook event
        result = payment_service.handle_webhook(webhook_data, provider='razorpay')
        
        # Mark event as processed
        payment_service.db.mark_event_processed(event_id, 'razorpay')
        
        return {
            'status': 'success', 
            'message': f'Processed {event_type} event',
            'result': result
        }, 200
        
    except Exception as e:
        logger.error(f"Error handling Razorpay webhook: {str(e)}")
        logger.error(f"Request data: {request.data}")
        return {'error': str(e)}, 500