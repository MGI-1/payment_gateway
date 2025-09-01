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

logger = setup_logging('payment_gateway')

# Environment detection using existing FLASK_ENV
FLASK_ENV = os.getenv('FLASK_ENV', 'development')

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

#not required
#PAYPAL_WEBHOOK_SECRET = os.getenv('PAYPAL_WEBHOOK_SECRET','')

# PayPal Environment based on FLASK_ENV
PAYPAL_BASE_URL = (
    "https://api.sandbox.paypal.com" if FLASK_ENV == 'development' 
    else "https://api.paypal.com"
)

# Database table names
DB_TABLE_SUBSCRIPTION_PLANS = 'subscription_plans'
DB_TABLE_USER_SUBSCRIPTIONS = 'user_subscriptions'
DB_TABLE_SUBSCRIPTION_INVOICES = 'subscription_invoices'
DB_TABLE_SUBSCRIPTION_EVENTS = 'subscription_events_log'
DB_TABLE_RESOURCE_USAGE = 'resource_usage'


# API Base URL function - using your existing variable names
def get_api_base_url():
    """Get API base URL using the same variables as frontend"""
    
    # Priority 1: MarketFit variable (VITE_API_BASE_URL)
    vite_api_url = os.getenv('VITE_API_BASE_URL')
    logger.info(f"DEBUG: VITE_API_BASE_URL = {vite_api_url}")

    if vite_api_url:
        return vite_api_url
    
    # Priority 2: SalesWit variable (REACT_APP_API_URL)  
    react_api_url = os.getenv('REACT_APP_API_URL')
    if react_api_url:
        return react_api_url
    
    # Development fallback
    #if FLASK_ENV == 'development':
    #    return 'http://localhost:5000'
    
    # Production fallback: Azure App Service provides this automatically
    website_hostname = os.getenv('WEBSITE_HOSTNAME')
    if website_hostname:
        return f"https://{website_hostname}"
    
    # Should not reach here in proper deployment
    raise ValueError(
        "Either VITE_API_BASE_URL (MarketFit) or REACT_APP_API_URL (SalesWit) "
        "must be set in environment variables"
    )
WEBHOOK_BASE_URL = get_api_base_url()
#WEBHOOK_BASE_URL = 'https://mf-backend-a0a5ama9fddqgtd8.centralus-01.azurewebsites.net'
# ADD these new environment variables
PAYPAL_WEBHOOK_ID = os.getenv('PAYPAL_WEBHOOK_ID', '')
# PayPal return URLs
PAYPAL_RETURN_URL = f"{WEBHOOK_BASE_URL}/api/subscriptions/paypal-success"
PAYPAL_CANCEL_URL = f"{WEBHOOK_BASE_URL}/api/subscriptions/paypal-cancel"

def get_frontend_url():
    """Get frontend URL for redirects"""
    frontend_url = os.getenv('FRONTEND_URL')
    if frontend_url:
        return frontend_url.rstrip('/')
    
    raise ValueError("FRONTEND_URL environment variable must be set")