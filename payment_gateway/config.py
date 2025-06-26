"""
Configuration for the payment gateway package.
"""
import os
import logging
from datetime import datetime

# Logging configuration
def setup_logging(name='payment_gateway', log_file=None):
    """Set up logging for the payment gateway"""
    if log_file is None:
        date_str = datetime.now().strftime('%Y%m%d')
        log_file = f'payment_gateway_{date_str}.log'
    
    logger = logging.getLogger(name)
    
    # Check if logger already has handlers to avoid duplicates
    if not logger.handlers:
        handler = logging.FileHandler(log_file)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    
    return logger

# Default database configuration (will be overridden by app)
DEFAULT_DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'app_database')
}

# Payment gateway credentials
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET', '')
RAZORPAY_WEBHOOK_SECRET = os.getenv('RAZORPAY_WEBHOOK_SECRET', '')

# PayPal credentials would be added here
PAYPAL_CLIENT_ID = os.getenv('PAYPAL_CLIENT_ID', '')
PAYPAL_CLIENT_SECRET = os.getenv('PAYPAL_CLIENT_SECRET', '')

# Database table names
DB_TABLE_SUBSCRIPTION_PLANS = 'subscription_plans'
DB_TABLE_USER_SUBSCRIPTIONS = 'user_subscriptions'
DB_TABLE_SUBSCRIPTION_INVOICES = 'subscription_invoices'
DB_TABLE_SUBSCRIPTION_EVENTS = 'subscription_events_log'
DB_TABLE_RESOURCE_USAGE = 'resource_usage'