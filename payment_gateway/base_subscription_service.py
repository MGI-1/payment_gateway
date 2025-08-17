"""
Base subscription service with shared methods
Used by both PaymentService and PayPalService to eliminate duplication
"""
import json
import logging
import traceback
from datetime import datetime, timedelta,timezone
from decimal import Decimal

from .db import DatabaseManager
from .utils.helpers import generate_id, parse_json_field, calculate_period_end
from .config import setup_logging, DB_TABLE_SUBSCRIPTION_PLANS, DB_TABLE_USER_SUBSCRIPTIONS, DB_TABLE_RESOURCE_USAGE

logger = logging.getLogger('payment_gateway')

class BaseSubscriptionService:
    """
    Base service class with shared subscription management methods
    """
    
    def __init__(self, db_config=None):
        """Initialize the base service"""
        self.db = DatabaseManager(db_config)
        setup_logging()

    def _ensure_float(self, value):
        """Convert Decimal/any numeric type to float for calculations"""
        if isinstance(value, Decimal):
            return float(value)
        return float(value) if value is not None else 0.0

    # =============================================================================
    # SHARED DATABASE METHODS
    # =============================================================================

    def _get_plan(self, plan_id):
        """Get plan details with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(
                f"SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS} WHERE razorpay_plan_id = %s OR paypal_plan_id = %s",
                (plan_id, plan_id)
            )
            plan = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return plan
            
        except Exception as e:
            logger.error(f"Error getting plan: {str(e)}")
            raise

    def _get_user_info(self, user_id):
        """Get user info with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("SELECT google_uid, email, display_name FROM users WHERE id = %s OR google_uid = %s", (user_id, user_id))
            user = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return user
            
        except Exception as e:
            logger.error(f"Error getting user info: {str(e)}")
            raise

    def _get_subscription_details(self, subscription_id):
        """Get subscription details with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT us.*, sp.name as plan_name, sp.amount, sp.currency, sp.interval
                FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
                JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
                WHERE us.id = %s
            """, (subscription_id,))
            
            subscription = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting subscription details: {str(e)}")
            raise

    def _get_subscription_for_cancellation(self, user_id, subscription_id):
        """Get subscription for cancellation with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE id = %s AND user_id = %s
            """, (subscription_id, user_id))
            
            subscription = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            if not subscription:
                logger.error(f"Subscription not found or not owned by user: {subscription_id}")
                raise ValueError(f"Subscription not found or not owned by user")
            
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting subscription for cancellation: {str(e)}")
            raise

    def _update_subscription_plan(self, subscription_id, new_plan_id):
        """Update subscription plan in database"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET plan_id = %s, updated_at = NOW()
                WHERE id = %s
            """, (new_plan_id, subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating subscription plan: {str(e)}")
            raise

    def _get_subscription_with_features(self, subscription_id):
        """Get subscription with features using isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT us.*, sp.features, sp.app_id 
                FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
                JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
                WHERE us.id = %s
            """, (subscription_id,))
            
            subscription = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting subscription with features: {str(e)}")
            raise

    def get_current_usage(self, user_id, subscription_id, app_id):
        """Get current resource usage for proration calculation"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT 
                    document_pages_quota,
                    perplexity_requests_quota,
                    requests_quota,
                    original_document_pages_quota,
                    original_perplexity_requests_quota,
                    original_requests_quota,
                    current_addon_document_pages,
                    current_addon_perplexity_requests,
                    current_addon_requests,
                    billing_period_start,
                    billing_period_end
                FROM {DB_TABLE_RESOURCE_USAGE}
                WHERE user_id = %s AND subscription_id = %s AND app_id = %s
                ORDER BY created_at DESC LIMIT 1
            """, (user_id, subscription_id, app_id))
            
            usage = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return usage
            
        except Exception as e:
            logger.error(f"Error getting current usage: {str(e)}")
            return None

    # =============================================================================
    # SHARED RESOURCE QUOTA METHODS
    # =============================================================================

    def initialize_resource_quota(self, user_id, subscription_id, app_id):
        """Initialize or reset resource quota for a subscription period"""
        try:
            subscription_details = self._get_subscription_with_features(subscription_id)
            
            if not subscription_details:
                logger.error(f"Subscription {subscription_id} not found")
                return False
            
            # Parse features
            features = self._parse_subscription_features(subscription_details.get('features', '{}'))
            
            # Set quota based on app
            quota_values = self._calculate_quota_values(app_id, features)
            
            # Create or update quota record with original values
            return self._save_quota_record_with_originals(user_id, subscription_id, app_id, subscription_details, quota_values)
            
        except Exception as e:
            logger.error(f"Error initializing resource quota: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _parse_subscription_features(self, features_str):
        """Parse subscription features JSON"""
        if isinstance(features_str, str):
            try:
                return json.loads(features_str)
            except json.JSONDecodeError:
                return {}
        return features_str or {}

    def _calculate_quota_values(self, app_id, features):
        """Calculate quota values based on app and features"""
        if app_id == 'marketfit':
            return {
                'document_pages_quota': features.get('document_pages', 40),
                'perplexity_requests_quota': features.get('perplexity_requests', 2),
                'requests_quota': 0
            }
        else:  # saleswit
            return {
                'document_pages_quota': 0,
                'perplexity_requests_quota': 0,
                'requests_quota': features.get('requests', 2)
            }

    def _save_quota_record_with_originals(self, user_id, subscription_id, app_id, subscription_details, quota_values):
        """Save or update quota record with original quota tracking"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Check if existing record exists
            cursor.execute(f"""
                SELECT id FROM {DB_TABLE_RESOURCE_USAGE}
                WHERE user_id = %s AND subscription_id = %s AND app_id = %s
                ORDER BY created_at DESC LIMIT 1
            """, (user_id, subscription_id, app_id))
            
            quota_record = cursor.fetchone()
            
            if quota_record:
                # Update existing record
                cursor.execute(f"""
                    UPDATE {DB_TABLE_RESOURCE_USAGE}
                    SET document_pages_quota = %s,
                        perplexity_requests_quota = %s,
                        requests_quota = %s,
                        original_document_pages_quota = %s,
                        original_perplexity_requests_quota = %s,
                        original_requests_quota = %s,
                        current_addon_document_pages = 0,
                        current_addon_perplexity_requests = 0,
                        current_addon_requests = 0,
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    quota_values['document_pages_quota'],
                    quota_values['perplexity_requests_quota'],
                    quota_values['requests_quota'],
                    quota_values['document_pages_quota'],  # Set original to new values
                    quota_values['perplexity_requests_quota'],
                    quota_values['requests_quota'],
                    quota_record['id']
                ))
                logger.info(f"Updated existing quota record {quota_record['id']}")
            else:
                # Create new record
                cursor.execute(f"""
                    INSERT INTO {DB_TABLE_RESOURCE_USAGE}
                    (user_id, subscription_id, app_id, billing_period_start, billing_period_end,
                    document_pages_quota, perplexity_requests_quota, requests_quota,
                    original_document_pages_quota, original_perplexity_requests_quota, original_requests_quota,
                    current_addon_document_pages, current_addon_perplexity_requests, current_addon_requests)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0)
                """, (
                    user_id,
                    subscription_id,
                    app_id,
                    subscription_details.get('current_period_start') or datetime.now(),
                    subscription_details.get('current_period_end') or (datetime.now() + timedelta(days=30)),
                    quota_values['document_pages_quota'],
                    quota_values['perplexity_requests_quota'],
                    quota_values['requests_quota'],
                    quota_values['document_pages_quota'],  # Original quotas
                    quota_values['perplexity_requests_quota'],
                    quota_values['requests_quota']
                ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
            
        except Exception as e:
            logger.error(f"Error saving quota record: {str(e)}")
            raise

    def _add_temporary_resources(self, user_id, subscription_id, app_id):
        """Add double free plan resources temporarily"""
        try:
            free_plan = self._get_free_plan(app_id)
            if not free_plan:
                logger.warning(f"No free plan found for {app_id}")
                return
            
            free_features = parse_json_field(free_plan.get('features', '{}'))
            
            if app_id == 'marketfit':
                temp_doc_pages = free_features.get('document_pages', 40) * 2
                temp_perplexity = free_features.get('perplexity_requests', 2) * 2
                temp_requests = 0
            else:  # saleswit
                temp_doc_pages = 0
                temp_perplexity = 0
                temp_requests = free_features.get('requests', 2) * 2
            
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_RESOURCE_USAGE}
                SET document_pages_quota = document_pages_quota + %s,
                    perplexity_requests_quota = perplexity_requests_quota + %s,
                    requests_quota = requests_quota + %s,
                    updated_at = NOW()
                WHERE user_id = %s AND subscription_id = %s AND app_id = %s
            """, (temp_doc_pages, temp_perplexity, temp_requests, user_id, subscription_id, app_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Added temporary resources: {temp_doc_pages} docs, {temp_perplexity} perplexity, {temp_requests} requests")
            
        except Exception as e:
            logger.error(f"Error adding temporary resources: {str(e)}")

    def _get_free_plan(self, app_id):
        """Get free plan with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS}
                WHERE app_id = %s AND amount = 0 AND is_active = TRUE
                LIMIT 1
            """, (app_id,))
            
            free_plan = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return free_plan
            
        except Exception as e:
            logger.error(f"Error getting free plan: {str(e)}")
            raise

    # =============================================================================
    # SHARED PLAN METHODS
    # =============================================================================

    def _is_monthly_plan(self, plan):
        """Check if plan is monthly"""
        return plan.get('interval') == 'month' and plan.get('interval_count', 1) == 1

    def _is_annual_plan(self, plan):
        """Check if plan is annual"""
        return (plan.get('interval') == 'year' and plan.get('interval_count', 1) == 1) or \
            (plan.get('interval') == 'month' and plan.get('interval_count', 1) >= 12)

    def _calculate_value_remaining_percentage(self, billing_cycle_info, resource_info):
        """Calculate value remaining as percentage based on min of time left and resources left"""
        time_remaining_pct = billing_cycle_info['time_factor']
        resource_remaining_pct = 1 - resource_info['base_plan_consumed_pct']
        value_remaining_pct = min(time_remaining_pct, resource_remaining_pct)
        return round(max(0.0, value_remaining_pct), 6)  # 6 decimal places for percentage precision
    
    def get_available_plans(self, app_id='marketfit'):
        """
        Get all available subscription plans for an app
        
        Args:
            app_id: The application ID
            
        Returns:
            list: Available plans
        """
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT id, name, description, amount, currency, `interval`, 
                    interval_count, features, app_id, plan_type, payment_gateways,
                    paypal_plan_id, razorpay_plan_id
                FROM {DB_TABLE_SUBSCRIPTION_PLANS}
                WHERE app_id = %s AND is_active = TRUE
                ORDER BY amount ASC
            """, (app_id,))
            
            plans = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            # Process the plans - parse JSON fields
            for plan in plans:
                if plan.get('features'):
                    plan['features'] = parse_json_field(plan['features'])
                
                if plan.get('payment_gateways'):
                    plan['payment_gateways'] = parse_json_field(plan['payment_gateways'], ['razorpay'])
            
            return plans
        except Exception as e:
            logger.error(f"Error getting available plans: {str(e)}")
            logger.error(traceback.format_exc())
            return []
    
    def get_resource_quota(self, user_id, app_id):
       """Get remaining resource quota for a user in the current billing period."""
       
       try:
           # Initialize quota object based on app
           quota = self._initialize_quota_object(app_id)
           
           # Get active subscription
           subscription_id = self._get_active_subscription_id(user_id, app_id)
           if not subscription_id:
               logger.warning(f"[AZURE DEBUG] No active subscription found for user {user_id}")
               return quota
           
           # Get quota record
           quota_result = self._get_quota_record(user_id, subscription_id, app_id)
           if quota_result:
               quota = self._update_quota_from_record(app_id, quota, quota_result)
           
           return quota
           
       except Exception as e:
           logger.error(f"[AZURE DEBUG] Error in get_resource_quota: {str(e)}")
           logger.error(traceback.format_exc())
           return self._initialize_quota_object(app_id)

    def check_resource_availability(self, user_id, app_id, resource_type, count=1):
        """Check if a user has enough resources for an action"""
        try:
            # Ensure user has a resource quota entry
            ensure_result = self.ensure_user_has_resource_quota(user_id, app_id)
            if not ensure_result:
                # This could be due to problematic subscription statuses or other errors
                return False
            
            # Get the user's resource quota
            quota = self.get_resource_quota(user_id, app_id)
            
            # Check if the quota is enough for the requested count
            if resource_type in quota:
                is_available = quota[resource_type] >= count
                return is_available
            
            # If resource type not found in quota, assume unavailable
            logger.warning(f"[AZURE DEBUG] Resource type {resource_type} not found in quota for user {user_id}")
            return False
                
        except Exception as e:
            logger.error(f"[AZURE DEBUG] Error in check_resource_availability: {str(e)}")
            logger.error(traceback.format_exc())
            # Default to not available on error
            return False

    def decrement_resource_quota(self, user_id, app_id, resource_type, count=1):
       """Decrement resource quota for a user."""
       
       try:
           # Check if resource is available
           if not self.check_resource_availability(user_id, app_id, resource_type, count):
               return False
           
           # Get subscription and quota record
           subscription_id = self._get_active_subscription_id(user_id, app_id)
           if not subscription_id:
               return False
           
           quota_record_id = self._get_quota_record_id(user_id, subscription_id, app_id)
           if not quota_record_id:
               # Initialize quota first
               init_result = self.initialize_resource_quota(user_id, subscription_id, app_id)
               if init_result:
                   return self.decrement_resource_quota(user_id, app_id, resource_type, count)
               return False
           
           # Decrement the quota
           return self._decrement_quota_record(quota_record_id, resource_type, count)
           
       except Exception as e:
           logger.error(f"[AZURE DEBUG] Error in decrement_resource_quota: {str(e)}")
           logger.error(traceback.format_exc())
           return False

    def ensure_user_has_resource_quota(self, user_id, app_id='marketfit'):
        """Ensure a user has a resource quota entry in the database."""
        
        try:
            # First check if there are any problematic subscription statuses
            status_issue = self._check_subscription_status_issues(user_id, app_id)
            if status_issue:
                logger.warning(f"[AZURE DEBUG] Cannot ensure quota - user {user_id} has {status_issue} subscription")
                return False
            
            # Get or create subscription (only returns active subscriptions)
            subscription = self._get_or_create_subscription(user_id, app_id)
            if not subscription:
                return False
            
            # Check if quota entry exists
            if self._quota_entry_exists(user_id, subscription['id'], app_id):
                return True
            
            # Create quota entry
            return self._create_quota_entry(user_id, subscription, app_id)
            
        except Exception as e:
            logger.error(f"[AZURE DEBUG] Error in ensure_user_has_resource_quota: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def get_billing_history(self, user_id, app_id):
       """
       Get billing history for a user
       
       Args:
           user_id: The user's ID
           app_id: The application ID
           
       Returns:
           list: Billing history
       """
       
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute("""
               SELECT i.* 
               FROM subscription_invoices i
               JOIN user_subscriptions s ON i.subscription_id = s.id
               WHERE i.user_id = %s AND s.app_id = %s
               ORDER BY i.invoice_date DESC
           """, (user_id, app_id))
           
           invoices = cursor.fetchall()
           cursor.close()
           conn.close()
           
           return invoices
           
       except Exception as e:
           logger.error(f"Error getting billing history: {str(e)}")
           logger.error(traceback.format_exc())
           return []

    def _get_plan_interval_details(self, plan_id):
        """Get plan interval details with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Fixed SQL by adding backticks around the reserved keyword 'interval'
            cursor.execute(f"""
                SELECT `interval`, interval_count
                FROM {DB_TABLE_SUBSCRIPTION_PLANS}
                WHERE id = %s
            """, (plan_id,))
            
            plan_details = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return plan_details
            
        except Exception as e:
            logger.error(f"Error getting plan interval details: {str(e)}")
            return None

    def _get_active_subscription(self, user_id, app_id):
        """Get active subscription with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT us.*, sp.name as plan_name, sp.features, sp.amount, sp.currency, sp.interval 
                FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
                JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
                WHERE us.user_id = %s AND us.app_id = %s AND us.status = 'active'
                ORDER BY us.created_at DESC LIMIT 1
            """, (user_id, app_id))
            
            subscription = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting active subscription: {str(e)}")
            raise

    def _get_pending_subscription(self, user_id, app_id):
        """Get pending subscription with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT us.*, sp.name as plan_name, sp.features, sp.amount, sp.currency, sp.interval 
                FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
                JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
                WHERE us.user_id = %s AND us.app_id = %s AND us.status = 'created'
                ORDER BY us.created_at DESC LIMIT 1
            """, (user_id, app_id))
            
            subscription = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting pending subscription: {str(e)}")
            raise

    def _parse_subscription_json_fields(self, subscription):
        """Parse JSON fields in subscription"""
        if subscription:
            if subscription.get('features'):
                subscription['features'] = parse_json_field(subscription['features'])
            
            if subscription.get('metadata'):
                subscription['metadata'] = parse_json_field(subscription['metadata'])
        
        return subscription
    
    def _get_active_subscription_id(self, user_id, app_id):
       """Get active subscription ID with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
               WHERE user_id = %s AND app_id = %s AND status = 'active'
               ORDER BY current_period_end DESC LIMIT 1
           """, (user_id, app_id))
           
           subscription_result = cursor.fetchone()
           
           cursor.close()
           conn.close()
           
           return subscription_result['id'] if subscription_result else None
           
       except Exception as e:
           logger.error(f"Error getting active subscription ID: {str(e)}")
           return None

    def _get_quota_record(self, user_id, subscription_id, app_id):
       """Get quota record with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT * FROM {DB_TABLE_RESOURCE_USAGE}
               WHERE user_id = %s AND subscription_id = %s AND app_id = %s
               ORDER BY created_at DESC LIMIT 1
           """, (user_id, subscription_id, app_id))
           
           quota_result = cursor.fetchone()
           
           cursor.close()
           conn.close()
           
           return quota_result
           
       except Exception as e:
           logger.error(f"Error getting quota record: {str(e)}")
           return None

    def _update_quota_from_record(self, app_id, quota, quota_result):
       """Update quota object from database record"""
       if app_id == 'marketfit':
           quota['document_pages'] = quota_result['document_pages_quota']
           quota['perplexity_requests'] = quota_result['perplexity_requests_quota']
       else:  # saleswit
           quota['requests'] = quota_result['requests_quota']
       
       return quota


    def _initialize_quota_object(self, app_id):
       """Initialize quota object based on app"""
       if app_id == 'marketfit':
           return {
               'document_pages': 0,
               'perplexity_requests': 0
           }
       else:  # saleswit
           return {
               'requests': 0
           }


    def _get_quota_record_id(self, user_id, subscription_id, app_id):
       """Get quota record ID with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT id FROM {DB_TABLE_RESOURCE_USAGE}
               WHERE user_id = %s AND subscription_id = %s AND app_id = %s
               ORDER BY created_at DESC LIMIT 1
           """, (user_id, subscription_id, app_id))
           
           quota_record = cursor.fetchone()
           
           cursor.close()
           conn.close()
           
           return quota_record['id'] if quota_record else None
           
       except Exception as e:
           logger.error(f"Error getting quota record ID: {str(e)}")
           return None

    def _decrement_quota_record(self, quota_record_id, resource_type, count):
       """Decrement quota record with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           # Update the quota by decrementing the specified resource
           column_name = f"{resource_type}_quota"
           update_query = f"""
               UPDATE {DB_TABLE_RESOURCE_USAGE}
               SET {column_name} = GREATEST(0, {column_name} - %s),
                   updated_at = NOW()
               WHERE id = %s
           """
           
           cursor.execute(update_query, (count, quota_record_id))
           conn.commit()
           
           cursor.close()
           conn.close()
           
           return True
           
       except Exception as e:
           logger.error(f"[AZURE DEBUG] Error updating quota: {str(e)}")
           logger.error(traceback.format_exc())
           return False

    def _get_or_create_subscription(self, user_id, app_id):
       """Get existing subscription or create free subscription"""
       try:
           # First try to get existing active subscription
           subscription = self._get_active_subscription_for_quota(user_id, app_id)
           if subscription:
               return subscription
           
           # No active subscription, check for free plan
           free_plan = self._get_free_plan(app_id)
           if not free_plan:
               logger.warning(f"[AZURE DEBUG] No free plan found for app {app_id}")
               return None
           
           # Create free subscription
           return self._create_free_subscription_for_quota(user_id, free_plan, app_id)
           
       except Exception as e:
           logger.error(f"Error getting or creating subscription: {str(e)}")
           raise

    def _get_active_subscription_for_quota(self, user_id, app_id):
       """Get active subscription for quota with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT id, plan_id, status, current_period_start, current_period_end 
               FROM {DB_TABLE_USER_SUBSCRIPTIONS}
               WHERE user_id = %s AND app_id = %s AND status = 'active'
               ORDER BY created_at DESC LIMIT 1
           """, (user_id, app_id))
           
           subscription = cursor.fetchone()
           
           cursor.close()
           conn.close()
           return subscription
           
       except Exception as e:
           logger.error(f"Error getting active subscription for quota: {str(e)}")
           raise

    def _check_subscription_status_issues(self, user_id, app_id):
        """
        Check if user has any subscription statuses that would block resource usage
        Includes statuses from both Razorpay and PayPal webhooks
        """
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # All statuses that indicate a subscription is not fully active
            problematic_statuses = [
                'created',          # Initial creation, not yet paid (Razorpay)
                'pending',          # Payment pending (Razorpay) 
                'halted',           # Payment failed, subscription suspended (Razorpay)
                'authenticated',    # Payment method authenticated but not active (Razorpay)
                'payment_failed',   # Failed payment (PayPal)
                'suspended'         # Suspended subscription (PayPal)
            ]
            
            status_list = ', '.join([f"'{status}'" for status in problematic_statuses])
            
            cursor.execute(f"""
                SELECT id, status FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE user_id = %s AND app_id = %s AND status IN ({status_list})
                ORDER BY created_at DESC LIMIT 1
            """, (user_id, app_id))
            
            problematic_subscription = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if problematic_subscription:
                logger.warning(f"[AZURE DEBUG] Found {problematic_subscription['status']} subscription for user {user_id}")
                return problematic_subscription['status']
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking subscription status issues: {str(e)}")
            return None

    def _create_free_subscription_for_quota(self, user_id, free_plan, app_id):
       """Create free subscription for quota with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           subscription_id = generate_id('sub_')
           current_period_start = datetime.now()
           current_period_end = current_period_start + timedelta(days=30)
           
           cursor.execute(f"""
               INSERT INTO {DB_TABLE_USER_SUBSCRIPTIONS}
               (id, user_id, plan_id, status, app_id, current_period_start, current_period_end)
               VALUES (%s, %s, %s, 'active', %s, %s, %s)
           """, (
               subscription_id, 
               user_id, 
               free_plan['id'], 
               app_id,
               current_period_start,
               current_period_end
           ))
           
           conn.commit()
           cursor.close()
           conn.close()
           
           
           return {
               'id': subscription_id,
               'plan_id': free_plan['id'],
               'current_period_start': current_period_start,
               'current_period_end': current_period_end
           }
           
       except Exception as e:
           logger.error(f"Error creating free subscription for quota: {str(e)}")
           raise

    def _quota_entry_exists(self, user_id, subscription_id, app_id):
       """Check if quota entry exists with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT id 
               FROM {DB_TABLE_RESOURCE_USAGE}
               WHERE user_id = %s AND subscription_id = %s AND app_id = %s
               ORDER BY created_at DESC LIMIT 1
           """, (user_id, subscription_id, app_id))
           
           quota_entry = cursor.fetchone()
           
           cursor.close()
           conn.close()
           
           return quota_entry is not None
           
       except Exception as e:
           logger.error(f"Error checking quota entry existence: {str(e)}")
           raise

    def _create_quota_entry(self, user_id, subscription, app_id):
       """Create quota entry with isolated connection"""
       try:
           # Get plan features
           plan_features = self._get_plan_features(subscription['plan_id'])
           
           # Calculate quota values
           features = self._parse_subscription_features(plan_features)
           quota_values = self._calculate_quota_values(app_id, features)
           
           # Create quota record
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           period_start = subscription.get('current_period_start') or datetime.now()
           period_end = subscription.get('current_period_end') or (datetime.now() + timedelta(days=30))
           
           cursor.execute(f"""
               INSERT INTO {DB_TABLE_RESOURCE_USAGE}
               (user_id, subscription_id, app_id, billing_period_start, billing_period_end,
               document_pages_quota, perplexity_requests_quota, requests_quota)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           """, (
               user_id,
               subscription['id'],
               app_id,
               period_start,
               period_end,
               quota_values['document_pages_quota'],
               quota_values['perplexity_requests_quota'],
               quota_values['requests_quota']
           ))
           
           conn.commit()
           cursor.close()
           conn.close()
           
           return True
           
       except Exception as e:
           logger.error(f"Error creating quota entry: {str(e)}")
           raise

    def _get_plan_features(self, plan_id):
       """Get plan features with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT features FROM {DB_TABLE_SUBSCRIPTION_PLANS}
               WHERE id = %s
           """, (plan_id,))
           
           plan = cursor.fetchone()
           
           cursor.close()
           conn.close()
           
           return plan['features'] if plan else '{}'
           
       except Exception as e:
           logger.error(f"Error getting plan features: {str(e)}")
           return '{}'
    

    def _calculate_subscription_period(self, subscription_data, plan_id):
        """Calculate subscription period dates"""
        start_date = datetime.now()
        
        # Try to get start date from payload
        start_at = subscription_data.get('start_at')
        if start_at:
            try:
                start_timestamp = int(start_at)
                start_date = datetime.fromtimestamp(start_timestamp, tz=timezone.utc)
            except (ValueError, TypeError):
               logger.error(f"Invalid start_at value: {start_at}")
               # Continue with current date as fallback
       
        # Get plan details for interval
        plan_details = self._get_plan_interval_details(plan_id)
       
       # Calculate period end based on plan
        if plan_details:
           period_end = calculate_period_end(
               start_date,
               plan_details['interval'],
               plan_details['interval_count']
           )
        else:
           # Default to 30 days if plan details not found
           period_end = start_date + timedelta(days=30)
       
        return start_date, period_end


    def _activate_subscription_with_period(self, subscription_id, start_date, period_end, subscription_data):
       """Activate subscription with period dates"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
               SET status = 'active', 
                   current_period_start = %s,
                   current_period_end = %s,
                   updated_at = NOW(),
                   metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
               WHERE razorpay_subscription_id = %s
           """, (start_date, period_end, json.dumps(subscription_data), subscription_id))
           
           conn.commit()
           cursor.close()
           conn.close()
           
       except Exception as e:
           logger.error(f"Error activating subscription with period: {str(e)}")
           raise       

    def get_user_subscription(self, user_id, app_id):
        """
        Get a user's active subscription for a specific app
        
        Args:
            user_id: The user's ID
            app_id: The application ID
            
        Returns:
            dict: Subscription details
        """
        logger.debug(f"Getting subscription for user {user_id}, app {app_id}")
        
        try:
            # Try active first
            subscription = self._get_active_subscription(user_id, app_id)
            
            # Try pending if no active
            if not subscription:
                subscription = self._get_pending_subscription(user_id, app_id)
            
            # Auto-create free if none found
            if not subscription:
                free_plan_id = f"plan_free_{app_id}"
                return self.create_subscription(user_id, free_plan_id, app_id)
            
            return self._parse_subscription_json_fields(subscription)
            
        except Exception as e:
            logger.error(f"Error getting user subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def _get_existing_subscription(self, user_id, app_id):
        """Get existing subscription with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS} 
                WHERE user_id = %s AND app_id = %s AND status = 'active'
            """, (user_id, app_id))
            
            existing = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return existing
            
        except Exception as e:
            logger.error(f"Error getting existing subscription: {str(e)}")
            raise

    def _handle_free_subscription(self, user_id, plan_id, app_id, plan, existing_subscription):
        """Handle free subscription creation with focused transaction"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Start transaction for free subscription
            try:
                if existing_subscription:
                    # User already has a subscription, update if it's not the same plan
                    if existing_subscription['plan_id'] != plan_id:
                        cursor.execute(f"""
                            UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                            SET plan_id = %s, current_period_start = NOW(), 
                                current_period_end = DATE_ADD(NOW(), INTERVAL %s MONTH)
                            WHERE id = %s
                        """, (plan_id, plan['interval_count'], existing_subscription['id']))
                        subscription_id = existing_subscription['id']
                    else:
                        subscription_id = existing_subscription['id']
                else:
                    # Create new subscription record
                    subscription_id = generate_id('sub_')
                    current_period_start = datetime.now()
                    current_period_end = calculate_period_end(
                        current_period_start, 
                        plan['interval'], 
                        plan['interval_count']
                    )
                    
                    cursor.execute(f"""
                        INSERT INTO {DB_TABLE_USER_SUBSCRIPTIONS}
                        (id, user_id, plan_id, status, current_period_start, current_period_end, app_id)
                        VALUES (%s, %s, %s, 'active', %s, %s, %s)
                    """, (subscription_id, user_id, plan_id, current_period_start, current_period_end, app_id))
                
                conn.commit()
                
                result = {
                    'id': subscription_id,
                    'user_id': user_id,
                    'plan_id': plan_id,
                    'status': 'active',
                    'app_id': app_id
                }
                
                cursor.close()
                conn.close()
                return result
                
            except Exception as e:
                conn.rollback()
                cursor.close()
                conn.close()
                raise
                
        except Exception as e:
            logger.error(f"Error creating free subscription: {str(e)}")
            raise

