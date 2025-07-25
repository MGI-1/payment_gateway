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

# NEW PRORATION FUNCTIONS

def calculate_billing_cycle_info(start_date, end_date):
    """
    Calculate billing cycle timing information
    
    Args:
        start_date: Billing period start
        end_date: Billing period end
        
    Returns:
        dict: Billing cycle information
    """
    current_date = datetime.now()
    
    total_days = (end_date - start_date).days
    elapsed_days = (current_date - start_date).days
    remaining_days = (end_date - current_date).days
    
    # Ensure non-negative values
    elapsed_days = max(0, elapsed_days)
    remaining_days = max(0, remaining_days)
    
    return {
        'days_total': total_days,
        'days_elapsed': elapsed_days,
        'days_remaining': remaining_days,
        'time_factor': remaining_days / total_days if total_days > 0 else 0
    }

def calculate_resource_utilization(usage_data, plan_features, app_id):
    """
    Calculate resource utilization for proration
    Only consider BASE PLAN usage, not addon usage
    
    Args:
        usage_data: Current resource usage data
        plan_features: Plan feature limits
        app_id: Application ID
        
    Returns:
        dict: Resource utilization data
    """
    if app_id == 'marketfit':
        # Base plan quotas (what user originally got)
        original_doc_pages = usage_data.get('original_document_pages_quota', 0)
        original_perplexity = usage_data.get('original_perplexity_requests_quota', 0)
        
        # Current total quotas (base + addon)
        current_total_doc_pages = usage_data.get('document_pages_quota', 0)
        current_total_perplexity = usage_data.get('perplexity_requests_quota', 0)
        
        # Addon contributions
        addon_doc_pages = usage_data.get('current_addon_document_pages', 0)
        addon_perplexity = usage_data.get('current_addon_perplexity_requests', 0)
        
        # Calculate base plan usage only
        base_doc_pages_remaining = current_total_doc_pages - addon_doc_pages
        base_perplexity_remaining = current_total_perplexity - addon_perplexity
        
        # Base plan consumption
        base_doc_used = original_doc_pages - base_doc_pages_remaining
        base_perplexity_used = original_perplexity - base_perplexity_remaining
        
        # Ensure non-negative values
        base_doc_used = max(0, base_doc_used)
        base_perplexity_used = max(0, base_perplexity_used)
        
        # Base plan consumption percentages
        base_doc_consumed_pct = base_doc_used / original_doc_pages if original_doc_pages > 0 else 0
        base_perplexity_consumed_pct = base_perplexity_used / original_perplexity if original_perplexity > 0 else 0
        
        # Average base plan consumption (equal weightage)
        avg_base_consumed_pct = (base_doc_consumed_pct + base_perplexity_consumed_pct) / 2
        
        return {
            'resource_factor': 1 - avg_base_consumed_pct,
            'base_plan_consumed_pct': avg_base_consumed_pct,
            'base_doc_consumed_pct': base_doc_consumed_pct,
            'base_perplexity_consumed_pct': base_perplexity_consumed_pct,
            'base_doc_used': base_doc_used,
            'base_perplexity_used': base_perplexity_used
        }
    
    else:  # saleswit
        # Single resource
        original_requests = usage_data.get('original_requests_quota', 0)
        current_total_requests = usage_data.get('requests_quota', 0)
        addon_requests = usage_data.get('current_addon_requests', 0)
        
        base_requests_remaining = current_total_requests - addon_requests
        base_requests_used = max(0, original_requests - base_requests_remaining)
        base_requests_consumed_pct = base_requests_used / original_requests if original_requests > 0 else 0
        
        return {
            'resource_factor': 1 - base_requests_consumed_pct,
            'base_plan_consumed_pct': base_requests_consumed_pct,
            'base_requests_used': base_requests_used
        }

def calculate_advanced_proration(current_plan, new_plan, billing_cycle_info, resource_info, minimum_charge=50):
    """Calculate proration with proper decimal handling"""
    time_consumed_pct = 1 - billing_cycle_info['time_factor']
    resource_consumed_pct = resource_info['base_plan_consumed_pct']
    
    billing_cycle_consumed_pct = max(time_consumed_pct, resource_consumed_pct)
    remaining_billing_cycle_pct = 1 - billing_cycle_consumed_pct
    
    # ✅ FIX: Convert Decimal to float for calculations
    current_amount = float(current_plan['amount']) if current_plan['amount'] else 0
    new_amount = float(new_plan['amount']) if new_plan['amount'] else 0
    
    price_difference = new_amount - current_amount
    
    if price_difference <= 0:
        return {
            'is_downgrade': True,
            'message': 'To downgrade your plan, please contact our support team.',
            'current_plan': current_plan['name'],
            'requested_plan': new_plan['name'],
            'contact_info': 'support@yourcompany.com',
            'action_required': 'contact_support'
        }
    else:
        prorated_amount = price_difference * remaining_billing_cycle_pct
        if 0 < prorated_amount < minimum_charge:
            prorated_amount = minimum_charge

    return {
        'prorated_amount': round(prorated_amount, 2),
        'price_difference': price_difference,
        'time_consumed_pct': time_consumed_pct,
        'resource_consumed_pct': resource_consumed_pct,
        'billing_cycle_consumed_pct': billing_cycle_consumed_pct,
        'remaining_billing_cycle_pct': remaining_billing_cycle_pct,
        'proration_method': 'time' if time_consumed_pct > resource_consumed_pct else 'resource',
        'is_upgrade': True,
        'billing_cycle_info': billing_cycle_info,
        'resource_info': resource_info
    }