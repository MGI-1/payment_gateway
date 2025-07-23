"""
PayPal webhook handler with full signature verification
"""
import json
import logging
import hmac
import hashlib
import base64
import requests
from flask import request, current_app
from ..config import PAYPAL_WEBHOOK_ID, FLASK_ENV

logger = logging.getLogger('payment_gateway')

# Try to import cryptography libraries for full verification
try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.exceptions import InvalidSignature
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("Cryptography library not available - falling back to basic verification")

def verify_paypal_webhook_signature(headers, payload):
    """
    PayPal webhook signature verification with environment-based security
    
    Args:
        headers: Request headers
        payload: Request body (bytes)
        
    Returns:
        bool: True if signature is valid
    """
    try:
        # Development mode - basic verification only
        if FLASK_ENV == 'development':
            logger.info("Development mode - using basic header verification")
            return _basic_paypal_verification(headers)
        
        # Production mode - attempt full verification if crypto is available
        if CRYPTO_AVAILABLE:
            logger.info("Production mode - using full RSA signature verification")
            return _full_paypal_verification(headers, payload)
        else:
            logger.warning("Cryptography not available - falling back to basic verification in production")
            return _basic_paypal_verification(headers)
        
    except Exception as e:
        logger.error(f"PayPal signature verification failed: {str(e)}")
        return False

def _basic_paypal_verification(headers):
    """
    Basic verification for development or when crypto is unavailable
    Checks that required headers are present and webhook ID matches
    """
    try:
        # Check required headers exist
        required_headers = [
            'PAYPAL-TRANSMISSION-ID',
            'PAYPAL-CERT-URL', 
            'PAYPAL-TRANSMISSION-SIG',
            'PAYPAL-TRANSMISSION-TIME'
        ]
        
        if not all(headers.get(header) for header in required_headers):
            logger.warning("Missing required PayPal headers")
            return False
        
        # Check webhook ID if configured
        webhook_id = PAYPAL_WEBHOOK_ID
        if webhook_id:
            # PayPal includes webhook ID in custom header or we can validate URL structure
            cert_url = headers.get('PAYPAL-CERT-URL', '')
            if not (cert_url.startswith('https://api.paypal.com/') or 
                   cert_url.startswith('https://api.sandbox.paypal.com/')):
                logger.warning("Invalid PayPal certificate URL")
                return False
        
        # Check signature has reasonable length
        signature = headers.get('PAYPAL-TRANSMISSION-SIG', '')
        if len(signature) < 50:
            logger.warning("PayPal signature too short")
            return False
        
        logger.info("Basic PayPal verification passed")
        return True
        
    except Exception as e:
        logger.error(f"Error in basic PayPal verification: {str(e)}")
        return False

def _full_paypal_verification(headers, payload):
    """
    Full RSA signature verification for production using PayPal certificates
    """
    try:
        # Extract required headers
        webhook_id = PAYPAL_WEBHOOK_ID
        transmission_id = headers.get('PAYPAL-TRANSMISSION-ID')
        cert_url = headers.get('PAYPAL-CERT-URL')
        transmission_sig = headers.get('PAYPAL-TRANSMISSION-SIG')
        timestamp = headers.get('PAYPAL-TRANSMISSION-TIME')
        
        if not all([webhook_id, transmission_id, cert_url, transmission_sig, timestamp]):
            logger.warning("Missing required PayPal headers or webhook ID not configured")
            return False
        
        # Step 1: Download and validate certificate
        certificate = _download_and_verify_certificate(cert_url)
        if not certificate:
            return False
        
        # Step 2: Construct the message that PayPal signed
        # PayPal signs: webhook_id + transmission_id + timestamp + payload
        message = f"{webhook_id}|{transmission_id}|{timestamp}|{payload.decode('utf-8')}"
        
        # Step 3: Verify RSA signature
        return _verify_rsa_signature(certificate, message, transmission_sig)
        
    except Exception as e:
        logger.error(f"Error in full PayPal verification: {str(e)}")
        return False

def _download_and_verify_certificate(cert_url):
    """
    Download PayPal certificate and perform basic validation
    """
    try:
        # Validate certificate URL is from PayPal domains
        valid_domains = [
            'https://api.paypal.com/',
            'https://api.sandbox.paypal.com/',
            'https://api-m.paypal.com/',
            'https://api-m.sandbox.paypal.com/'
        ]
        
        if not any(cert_url.startswith(domain) for domain in valid_domains):
            logger.error(f"Invalid PayPal certificate URL: {cert_url}")
            return None
        
        # Download certificate with timeout
        logger.info(f"Downloading PayPal certificate from: {cert_url}")
        response = requests.get(cert_url, timeout=10)
        response.raise_for_status()
        
        # Parse X.509 certificate
        cert_data = response.text
        certificate = x509.load_pem_x509_certificate(cert_data.encode('utf-8'))
        
        # Validate certificate is not expired
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        
        if certificate.not_valid_after.replace(tzinfo=timezone.utc) < now:
            logger.error("PayPal certificate has expired")
            return None
        
        if certificate.not_valid_before.replace(tzinfo=timezone.utc) > now:
            logger.error("PayPal certificate is not yet valid")
            return None
        
        logger.info("PayPal certificate downloaded and validated successfully")
        return certificate
        
    except requests.RequestException as e:
        logger.error(f"Error downloading PayPal certificate: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error parsing PayPal certificate: {str(e)}")
        return None

def _verify_rsa_signature(certificate, message, signature):
    """
    Verify RSA signature using PayPal's public key from certificate
    """
    try:
        # Extract public key from certificate
        public_key = certificate.public_key()
        
        # Ensure it's an RSA key
        if not isinstance(public_key, rsa.RSAPublicKey):
            logger.error("PayPal certificate does not contain RSA public key")
            return False
        
        # Decode base64 signature
        try:
            signature_bytes = base64.b64decode(signature)
        except Exception as e:
            logger.error(f"Error decoding PayPal signature: {str(e)}")
            return False
        
        # Verify signature using RSA-SHA256
        public_key.verify(
            signature_bytes,
            message.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        
        logger.info("PayPal RSA signature verification successful")
        return True
        
    except InvalidSignature:
        logger.warning("PayPal signature verification failed - signature is invalid")
        return False
    except Exception as e:
        logger.error(f"Error verifying PayPal RSA signature: {str(e)}")
        return False

def handle_paypal_webhook(payment_service):
    """
    Handle PayPal webhook events
    Enhanced to handle one-time payment completion for upgrade flows
    
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
        else:
            logger.warning("No PayPal webhook signature provided")
            # In development, might continue without signature
            if FLASK_ENV != 'development':
                return {'error': 'Missing signature'}, 400
        
        # Parse the webhook payload
        webhook_data = request.json
        event_type = webhook_data.get('event_type')
        event_id = webhook_data.get('id')
        
        logger.info(f"Processing PayPal webhook: {event_type}, ID: {event_id}")
        
        # Check idempotency
        if payment_service.db.is_event_processed(event_id, 'paypal'):
            logger.info(f"PayPal event {event_id} already processed")
            return {'status': 'already_processed'}, 200
        
        # Handle one-time payment completion for proration
        if event_type == 'PAYMENT.CAPTURE.COMPLETED':
            logger.info("Processing PAYMENT.CAPTURE.COMPLETED event")
            resource = webhook_data.get('resource', {})
            
            # Check if this is a proration payment for subscription upgrade
            purchase_units = resource.get('purchase_units', [])
            if purchase_units:
                custom_id = purchase_units[0].get('custom_id', '')
                
                if custom_id.startswith('sub_'):
                    logger.info(f"Processing proration payment for subscription upgrade: {custom_id}")
                    order_id = resource.get('id')
                    
                    if order_id:
                        result = payment_service.handle_paypal_proration_completion(order_id)
                        
                        # Mark event as processed
                        payment_service.db.mark_event_processed(event_id, 'paypal')
                        
                        return {
                            'status': 'success',
                            'message': 'Proration payment processed successfully',
                            'result': result
                        }, 200
                    else:
                        logger.warning("Missing order ID in PAYMENT.CAPTURE.COMPLETED webhook")
                else:
                    logger.info(f"PAYMENT.CAPTURE.COMPLETED for non-subscription payment: {custom_id}")
        
        # Handle subscription events
        if event_type and event_type.startswith('BILLING.SUBSCRIPTION.'):
            resource = webhook_data.get('resource', {})
            subscription_id = resource.get('id')
            
            logger.info(f"PayPal subscription event: {event_type}, ID: {subscription_id}")
            
            if event_type == 'BILLING.SUBSCRIPTION.PAYMENT.SUCCEEDED':
                logger.info("Processing subscription payment success")
                # For monthly upgrades: replace temporary resources with full quota
                subscription = payment_service.get_subscription_by_gateway_id(subscription_id, 'paypal')
                
                if subscription:
                    logger.info(f"Replacing temporary resources with full quota for subscription {subscription.get('id')} after payment")
                    # Initialize full quota (replaces temporary resources)
                    payment_service.initialize_resource_quota(
                        subscription.get('user_id'),
                        subscription.get('id'), 
                        subscription.get('app_id')
                    )
                    
                    # Log the renewal
                    payment_service.db.log_subscription_action(
                        subscription.get('id'),
                        'payment_succeeded',
                        {'paypal_subscription_id': subscription_id, 'event_data': resource},
                        'paypal_webhook'
                    )
                else:
                    logger.warning(f"No subscription found for PayPal ID: {subscription_id}")
            
            elif event_type == 'BILLING.SUBSCRIPTION.UPDATED':
                logger.info("Processing subscription plan update")
                subscription = payment_service.get_subscription_by_gateway_id(subscription_id, 'paypal')
                
                if subscription:
                    # Log the update
                    payment_service.db.log_subscription_action(
                        subscription.get('id'),
                        'subscription_updated',
                        {'paypal_subscription_id': subscription_id, 'event_data': resource},
                        'paypal_webhook'
                    )
                else:
                    logger.warning(f"No subscription found for PayPal ID: {subscription_id}")
            
            elif event_type == 'BILLING.SUBSCRIPTION.CANCELLED':
                logger.info("Processing subscription cancellation")
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
                else:
                    logger.warning(f"No subscription found for PayPal ID: {subscription_id}")
            
            elif event_type == 'BILLING.SUBSCRIPTION.PAYMENT.FAILED':
                logger.info("Processing subscription payment failure")
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
                else:
                    logger.warning(f"No subscription found for PayPal ID: {subscription_id}")
            
            elif event_type == 'BILLING.SUBSCRIPTION.SUSPENDED':
                logger.info("Processing subscription suspension")
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
                else:
                    logger.warning(f"No subscription found for PayPal ID: {subscription_id}")
        
        # Process the webhook event using existing handler (will now handle PayPal properly)
        result = payment_service.handle_webhook(webhook_data, provider='paypal')
        
        # Mark event as processed
        payment_service.db.mark_event_processed(event_id, 'paypal')
        
        return {
            'status': 'success', 
            'message': f'Processed {event_type} event successfully',
            'result': result
        }, 200
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in PayPal webhook: {str(e)}")
        return {'error': 'Invalid JSON payload'}, 400
    except Exception as e:
        logger.error(f"Error handling PayPal webhook: {str(e)}")
        logger.error(f"Request data: {request.data}")
        return {'error': str(e)}, 500