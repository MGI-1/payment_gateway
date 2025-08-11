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
    Handle Razorpay webhook events - HTTP layer only
    All business logic delegated to service layer
    
    Args:
        payment_service: The PaymentService instance
        
    Returns:
        tuple: Response object and status code
    """
    try:
        # 1. Signature verification
        webhook_signature = request.headers.get('X-Razorpay-Signature')
        payload = request.data
        
        logger.info(f"Received Razorpay webhook, payload length: {len(payload)}")
        
        if webhook_signature:
            if not verify_razorpay_signature(payload, webhook_signature):
                logger.warning("Invalid Razorpay webhook signature")
                return {'error': 'Invalid signature'}, 400
        
        # 2. Parse payload
        webhook_data = request.json
        event_type = webhook_data.get('event')
        
        # 3. Generate event ID for idempotency
        event_id = f"razorpay_{event_type}_{webhook_data.get('created_at', '')}"
        if 'payload' in webhook_data and 'subscription' in webhook_data['payload']:
            sub_data = webhook_data['payload']['subscription']
            sub_id = sub_data.get('entity', {}).get('id') if 'entity' in sub_data else sub_data.get('id')
            if sub_id:
                event_id = f"razorpay_{event_type}_{sub_id}_{webhook_data.get('created_at', '')}"
        
        logger.info(f"Processing Razorpay webhook: {event_type}, Event ID: {event_id}")
        
        # 4. Idempotency check
        if payment_service.db.is_event_processed(event_id, 'razorpay'):
            logger.info(f"Razorpay event {event_id} already processed")
            return {'status': 'already_processed'}, 200
        
        # 5. Delegate ALL business logic to service layer
        result = payment_service.process_webhook_event(
            provider='razorpay',
            event_type=event_type,
            event_id=event_id,
            payload=webhook_data
        )
        
        # 6. Return HTTP response
        return {
            'status': 'success' if result.get('success') else 'error',
            'message': result.get('message', f'Processed {event_type} event'),
            'event_type': event_type
        }, 200 if result.get('success') else 500
        
    except Exception as e:
        logger.error(f"Error handling Razorpay webhook: {str(e)}")
        logger.error(f"Request data: {request.data}")
        return {'error': str(e)}, 500