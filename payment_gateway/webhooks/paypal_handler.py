"""
PayPal webhook handler
"""
import json
import logging
import hmac
import hashlib
from flask import request, current_app
from ..config import PAYPAL_WEBHOOK_SECRET

logger = logging.getLogger('payment_gateway')

def verify_paypal_webhook_signature(headers, payload):
    """
    Verify the PayPal webhook signature
    
    Args:
        headers: Request headers
        payload: Request body (bytes)
        
    Returns:
        bool: True if signature is valid
    """
    try:
        # Get PayPal signature headers
        webhook_id = headers.get('PAYPAL-TRANSMISSION-ID')
        cert_url = headers.get('PAYPAL-CERT-URL')
        transmission_sig = headers.get('PAYPAL-TRANSMISSION-SIG')
        timestamp = headers.get('PAYPAL-TRANSMISSION-TIME')
        
        if not all([webhook_id, cert_url, transmission_sig, timestamp]):
            logger.warning("Missing PayPal signature headers")
            return False
        
        # For now, implement basic verification
        # In production, implement full PayPal signature verification
        webhook_secret = PAYPAL_WEBHOOK_SECRET
        
        if not webhook_secret:
            logger.warning("PayPal webhook secret not configured")
            return False
        
        # Create expected signature (simplified)
        expected_signature = hmac.new(
            webhook_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, transmission_sig)
        
    except Exception as e:
        logger.error(f"PayPal signature verification failed: {str(e)}")
        return False

def handle_paypal_webhook(payment_service):
    """
    Handle PayPal webhook events
    
    Args:
        payment_service: The PaymentService instance
        
    Returns:
        tuple: Response object and status code
    """
    try:
        # Get webhook signature and verify
        webhook_signature = request.headers.get('PAYPAL-TRANSMISSION-SIG')
        payload = request.data
        
        if webhook_signature:
            if not verify_paypal_webhook_signature(request.headers, payload):
                logger.warning("Invalid PayPal webhook signature")
                return {'error': 'Invalid signature'}, 400
        
        # Parse the webhook payload
        webhook_data = request.json
        event_type = webhook_data.get('event_type')
        event_id = webhook_data.get('id')
        
        logger.info(f"Processing PayPal webhook: {event_type}, ID: {event_id}")
        
        # Check idempotency
        if payment_service.db.is_event_processed(event_id, 'paypal'):
            logger.info(f"PayPal event {event_id} already processed")
            return {'status': 'already_processed'}, 200
        
        # Handle subscription events
        if event_type and event_type.startswith('BILLING.SUBSCRIPTION.'):
            resource = webhook_data.get('resource', {})
            subscription_id = resource.get('id')
            
            logger.info(f"PayPal subscription event: {event_type}, ID: {subscription_id}")
            
            # Handle specific events
            if event_type == 'BILLING.SUBSCRIPTION.PAYMENT.SUCCEEDED':
                # Reset quota on subscription payment success
                subscription = payment_service.get_subscription_by_gateway_id(subscription_id, 'paypal')
                
                if subscription:
                    logger.info(f"Resetting quota for subscription {subscription.get('id')} after renewal")
                    payment_service.reset_quota_on_renewal(subscription.get('id'))
                    
                    # Log the renewal
                    payment_service.db.log_subscription_action(
                        subscription.get('id'),
                        'payment_succeeded',
                        {'paypal_subscription_id': subscription_id, 'event_data': resource},
                        'paypal_webhook'
                    )
            
            elif event_type == 'BILLING.SUBSCRIPTION.CANCELLED':
                # Handle subscription cancellation
                subscription = payment_service.get_subscription_by_gateway_id(subscription_id, 'paypal')
                
                if subscription:
                    payment_service._update_subscription_status_by_gateway_id(
                        subscription_id, 'cancelled', resource, 'paypal'
                    )
                    
                    # Log the cancellation
                    payment_service.db.log_subscription_action(
                        subscription.get('id'),
                        'cancelled',
                        {'paypal_subscription_id': subscription_id, 'event_data': resource},
                        'paypal_webhook'
                    )
            
            elif event_type == 'BILLING.SUBSCRIPTION.PAYMENT.FAILED':
                # Handle payment failure
                subscription = payment_service.get_subscription_by_gateway_id(subscription_id, 'paypal')
                
                if subscription:
                    payment_service._update_subscription_status_by_gateway_id(
                        subscription_id, 'payment_failed', resource, 'paypal'
                    )
                    
                    # Log the failure
                    payment_service.db.log_subscription_action(
                        subscription.get('id'),
                        'payment_failed',
                        {'paypal_subscription_id': subscription_id, 'event_data': resource},
                        'paypal_webhook'
                    )
            
            elif event_type == 'BILLING.SUBSCRIPTION.SUSPENDED':
                # Handle subscription suspension
                subscription = payment_service.get_subscription_by_gateway_id(subscription_id, 'paypal')
                
                if subscription:
                    payment_service._update_subscription_status_by_gateway_id(
                        subscription_id, 'suspended', resource, 'paypal'
                    )
                    
                    # Log the suspension
                    payment_service.db.log_subscription_action(
                        subscription.get('id'),
                        'suspended',
                        {'paypal_subscription_id': subscription_id, 'event_data': resource},
                        'paypal_webhook'
                    )
        
        # Process the webhook event (will now handle PayPal properly)
        result = payment_service.handle_webhook(webhook_data, provider='paypal')
        
        # Mark event as processed
        payment_service.db.mark_event_processed(event_id, 'paypal')
        
        return {
            'status': 'success', 
            'message': f'Processed {event_type} event',
            'result': result
        }, 200
        
    except Exception as e:
        logger.error(f"Error handling PayPal webhook: {str(e)}")
        logger.error(f"Request data: {request.data}")
        return {'error': str(e)}, 500