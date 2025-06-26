"""
Payment Gateway Integration Package

A shared package for payment gateway integrations including Razorpay and PayPal
to be used across different Flask applications.
"""

__version__ = '0.1.0'

from .service import PaymentService
from .routes import init_payment_routes

# Convenience function to initialize the payment service
def init_payment_gateway(app=None, db_config=None):
    """Initialize the payment gateway with a Flask app and database configuration"""
    service = PaymentService(app, db_config)
    
    if app:
        init_payment_routes(app, service)
    
    return service