"""
Payment Gateway Integration Package

A shared package for payment gateway integrations including Razorpay and PayPal
to be used across different Flask applications.
"""

__version__ = '0.1.0'

from .service import PaymentService
from .paypal_service import PayPalService
from .base_subscription_service import BaseSubscriptionService
from .routes import init_payment_routes

# Backward compatible function
def init_payment_gateway(app=None, db_config=None, return_both_services=False):
    """
    Initialize the payment gateway with a Flask app and database configuration
    
    Args:
        app: Flask application
        db_config: Database configuration
        return_both_services: If True, returns dict with both services. If False, returns only PaymentService for backward compatibility.
    
    Returns:
        PaymentService instance (default) or dict with both services
    """
    # Initialize both services
    payment_service = PaymentService(app, db_config)
    paypal_service = PayPalService(app, db_config)
    
    if app:
        # Initialize routes with both services
        init_payment_routes(app, payment_service, paypal_service)
    
    # For backward compatibility, return only payment_service by default
    if return_both_services:
        return {
            'payment_service': payment_service,
            'paypal_service': paypal_service
        }
    else:
        # Store paypal_service in payment_service for access if needed
        payment_service.paypal_service = paypal_service
        return payment_service

# New function for those who want both services explicitly
def init_both_payment_services(app=None, db_config=None):
    """Initialize both payment services and return as dictionary"""
    return init_payment_gateway(app, db_config, return_both_services=True)

# Alternative: Individual service initialization functions
def init_razorpay_service(app=None, db_config=None):
    """Initialize only Razorpay payment service"""
    return PaymentService(app, db_config)

def init_paypal_service(app=None, db_config=None):
    """Initialize only PayPal payment service"""
    return PayPalService(app, db_config)

# Export all services for direct import
__all__ = [
    'PaymentService',
    'PayPalService', 
    'BaseSubscriptionService',
    'init_payment_gateway',
    'init_both_payment_services',
    'init_razorpay_service',
    'init_paypal_service',
    'init_payment_routes'
]