"""
Helper utilities for payment gateway operations
"""
import json
import uuid
from datetime import datetime, timedelta

def generate_id(prefix=''):
    """Generate a unique ID with optional prefix"""
    return f"{prefix}{uuid.uuid4().hex}"

def calculate_period_end(start_date, interval, count=1):
    """
    Calculate the end date based on interval and count
    
    Args:
        start_date: Start date (datetime)
        interval: 'month' or 'year'
        count: Number of intervals
        
    Returns:
        datetime: End date
    """
    if interval == 'month':
        return start_date + timedelta(days=30 * count)
    elif interval == 'year':
        return start_date + timedelta(days=365 * count)
    else:
        return start_date + timedelta(days=30)  # Default to monthly

def parse_json_field(data, default=None):
    """
    Safely parse a JSON field
    
    Args:
        data: JSON string or None
        default: Default value if parsing fails
        
    Returns:
        dict or list: Parsed JSON data or default
    """
    if not data:
        return default or {}
        
    if isinstance(data, (dict, list)):
        return data
        
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return default or {}

def format_subscription_price(amount, currency='INR', interval=None):
    """
    Format a subscription price for display
    
    Args:
        amount: The amount in smallest currency unit
        currency: Currency code
        interval: Billing interval
        
    Returns:
        str: Formatted price string
    """
    # Convert paisa to rupees for INR
    display_amount = amount / 100 if currency == 'INR' else amount
    
    # Use basic formatting for now
    formatted_price = f"{currency} {display_amount:.0f}"
    
    if interval:
        return f"{formatted_price}/{interval}"
    
    return formatted_price