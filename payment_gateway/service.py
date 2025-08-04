"""
Main payment service class for payment gateway operations
"""
import json
import logging
import traceback
import os
from datetime import datetime, timedelta

from .db import DatabaseManager
from .providers.razorpay_provider import RazorpayProvider
from .providers.paypal_provider import PayPalProvider
from .utils.helpers import generate_id, calculate_period_end, calculate_billing_cycle_info, calculate_resource_utilization, calculate_advanced_proration,parse_json_field
from .config import setup_logging, DB_TABLE_SUBSCRIPTION_PLANS, DB_TABLE_USER_SUBSCRIPTIONS, DB_TABLE_RESOURCE_USAGE
logger = logging.getLogger('payment_gateway')

class PaymentService:
    """
    Service class to handle payment-related operations.
    This service is designed to work across multiple applications.
    """
    
    def __init__(self, app=None, db_config=None):
        """Initialize the payment service"""
        # Set up logging
        setup_logging()
        
        # Initialize database
        self.db = DatabaseManager(db_config)
        
        # Initialize providers
        self.razorpay = RazorpayProvider()
        self.paypal = PayPalProvider()
        
        # Initialize Flask app if provided
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app context"""
        self.app = app
        logger.info("Initializing PaymentService with Flask app")
        
        # Initialize database tables
        with app.app_context():
            self.db.init_tables()

    def _ensure_float(self, value):
        """Convert Decimal/any numeric type to float for calculations"""
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
        return float(value) if value is not None else 0.0

    def create_subscription(self, user_id, plan_id, app_id):
        """
        Create a subscription for a user.
        For free plans, just records it in the database.
        For paid plans, creates a payment gateway subscription.
        
        Args:
            user_id: The user's ID
            plan_id: The plan ID
            app_id: The application ID
            
        Returns:
            dict: Subscription details
        """
        logger.info(f"Creating subscription for user {user_id}, plan {plan_id}, app {app_id}")
        
        try:
            # Phase 1: Get required data (separate connections)
            plan = self._get_plan(plan_id)
            if not plan:
                raise ValueError(f"Plan with ID {plan_id} not found")
            
            existing_subscription = self._get_existing_subscription(user_id, app_id)
            
            # Phase 2: Handle based on plan type
            if plan['amount'] == 0:
                return self._handle_free_subscription(user_id, plan_id, app_id, plan, existing_subscription)
            else:
                return self._handle_paid_subscription(user_id, plan_id, app_id, plan, existing_subscription)
                
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def _get_plan(self, plan_id):
        """Get plan details with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS} WHERE id = %s", (plan_id,))
            plan = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return plan
            
        except Exception as e:
            logger.error(f"Error getting plan: {str(e)}")
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
    def _calculate_value_remaining_percentage(self, billing_cycle_info, resource_info):
        """Calculate value remaining as percentage based on min of time left and resources left"""
        time_remaining_pct = billing_cycle_info['time_factor']
        resource_remaining_pct = 1 - resource_info['base_plan_consumed_pct']
        value_remaining_pct = min(time_remaining_pct, resource_remaining_pct)
        return max(0.0, value_remaining_pct)

    def _get_test_discount_for_value(self, value_remaining_pct):
        """Testing version of discount calculation - embedded in service"""
        logger.info(f"[TEST DISCOUNT] Calculating discount for {value_remaining_pct:.1f}% remaining value")
        
        if value_remaining_pct > 67:
            return {
                'error': True,
                'error_type': 'discount_too_high',
                'message': 'The remaining value is too high for automatic upgrade. Please contact support for assistance.',
                'action_required': 'contact_support'
            }
        elif value_remaining_pct > 50:
            logger.info("[TEST DISCOUNT] Applying 65% discount for high remaining value")
            return 65
        elif value_remaining_pct > 25:
            logger.info("[TEST DISCOUNT] Applying 45% discount for medium remaining value")
            return 45
        else:
            logger.info("[TEST DISCOUNT] Applying 20% discount for low remaining value")
            return 20
    
    def _get_discount_offer_for_value(self, value_remaining_as_pct_of_new_plan):
        """Get appropriate Razorpay discount offer based on value remaining - WITH TESTING OVERRIDE"""
        
        # ✨ NEW: Check for testing mode first
        if os.getenv('TESTING_DISCOUNT_MODE') == 'true':
            logger.info("[TESTING MODE] Using embedded test discount calculation")
            return self._get_test_discount_for_value(value_remaining_as_pct_of_new_plan)
        
        # ✅ UNCHANGED: Original production logic
        if value_remaining_as_pct_of_new_plan > 67:
            return {
                'error': True,
                'error_type': 'discount_too_high',
                'message': 'The remaining value is too high for automatic upgrade. Please contact support for assistance.',
                'action_required': 'contact_support'
            }
        
        available_discounts = [1, 4, 7, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 67]
        
        for discount in available_discounts:
            if value_remaining_as_pct_of_new_plan <= discount:
                return discount
        
        raise ValueError("Unexpected discount calculation error")

    def _get_currency_and_gateway_from_plan(self, plan):
        """Determine currency and preferred gateway from plan"""
        from .utils.helpers import parse_json_field  # Import the utility
        
        currency = plan.get('currency')
        
        if currency not in ['INR', 'USD']:
            raise ValueError({
                'error': True,
                'error_type': 'unsupported_currency',
                'message': 'Unsupported currency. Please contact support.',
                'action_required': 'contact_support'
            })
        
        # Use parse_json_field to safely handle JSON conversion
        payment_gateways = parse_json_field(plan.get('payment_gateways'), ['razorpay'])
        
        if currency == 'INR':
            return 'INR', 'razorpay'
        else:  # USD
            preferred_gateway = payment_gateways[0] if payment_gateways else 'paypal'
            return 'USD', preferred_gateway

    def _is_monthly_plan(self, plan):
        """Check if plan is monthly"""
        return plan.get('interval') == 'month' and plan.get('interval_count', 1) == 1

    def _is_annual_plan(self, plan):
        """Check if plan is annual"""
        return (plan.get('interval') == 'year' and plan.get('interval_count', 1) == 1) or \
            (plan.get('interval') == 'month' and plan.get('interval_count', 1) >= 12)

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

    def _handle_paid_subscription(self, user_id, plan_id, app_id, plan, existing_subscription):
        """Handle paid subscription creation"""
        try:
            # Phase 1: Get user info (separate connection)
            user = self._get_user_info(user_id)
            if not user:
                raise ValueError(f"User with ID {user_id} not found")
            
            # Phase 2: Create gateway subscription (outside database transaction)
            gateway_response = self._create_gateway_subscription(plan, user, app_id)
            
            # Phase 3: Save to database (focused transaction)
            return self._save_paid_subscription(user_id, plan_id, app_id, gateway_response)
            
        except Exception as e:
            logger.error(f"Error creating paid subscription: {str(e)}")
            raise

    def _get_user_info(self, user_id):
        """Get user info with isolated connection - THIS FIXES LINE 129"""
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

    def _create_gateway_subscription(self, plan, user, app_id):
        """Create subscription with payment gateway (no database operations)"""
        try:
            # Get payment gateways from plan
            payment_gateways = parse_json_field(plan.get('payment_gateways'), ['razorpay'])
            
            # Determine which gateway to use (use first in list)
            gateway = payment_gateways[0] if payment_gateways else 'razorpay'
            
            if gateway == 'razorpay':
                gateway_plan_id = plan.get('razorpay_plan_id') or plan['id']
                
                # DEBUG: Log the user object and what we're about to pass
                logger.info(f"[SERVICE DEBUG] user object from database: {user}")
                logger.info(f"[SERVICE DEBUG] user object types: {[(k, type(v)) for k, v in user.items()] if user else 'None'}")
                logger.info(f"[SERVICE DEBUG] app_id: {app_id}, type: {type(app_id)}")
                
                customer_info = {'user_id': user['google_uid'], 'email': user.get('email'), 'name': user.get('display_name')}
                logger.info(f"[SERVICE DEBUG] customer_info being passed: {customer_info}")
                logger.info(f"[SERVICE DEBUG] customer_info types: {[(k, type(v)) for k, v in customer_info.items()]}")
                
                response = self.razorpay.create_subscription(
                    gateway_plan_id,
                    customer_info,
                    app_id
                )    
                if response.get('error'):
                    raise ValueError(response.get('message', 'Failed to create Razorpay subscription'))
                
                response['gateway'] = gateway
                return response
                
            elif gateway == 'paypal':
                gateway_plan_id = plan.get('paypal_plan_id')
                
                if not gateway_plan_id:
                    raise ValueError("PayPal plan ID not found for this plan")
                
                response = self.paypal.create_subscription(
                    gateway_plan_id,
                    {'user_id': user['id'], 'email': user.get('email'), 'name': user.get('display_name')},
                    app_id
                )
                
                if response.get('error'):
                    raise ValueError(response.get('message', 'Failed to create PayPal subscription'))
                
                response['gateway'] = gateway
                return response
            
            else:
                raise ValueError(f"Unsupported payment gateway: {gateway}")
                
        except Exception as e:
            logger.error(f"Error creating gateway subscription: {str(e)}")
            raise

    def _save_paid_subscription(self, user_id, plan_id, app_id, gateway_response):
        """Save paid subscription to database with focused transaction"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Start focused transaction
            
            
            try:
                # Generate IDs
                subscription_id = generate_id('sub_')
                gateway_sub_id = gateway_response.get('id')
                gateway = gateway_response.get('gateway')
                
                # Set the appropriate field based on gateway
                razorpay_subscription_id = gateway_sub_id if gateway == 'razorpay' else None
                paypal_subscription_id = gateway_sub_id if gateway == 'paypal' else None
                
                # Insert subscription record
                cursor.execute(f"""
                    INSERT INTO {DB_TABLE_USER_SUBSCRIPTIONS}
                    (id, user_id, plan_id, razorpay_subscription_id, paypal_subscription_id, status, app_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, 'created', %s, %s)
                """, (
                    subscription_id, 
                    user_id, 
                    plan_id, 
                    razorpay_subscription_id,
                    paypal_subscription_id,
                    app_id, 
                    json.dumps(gateway_response)
                ))
                
                # Log the subscription creation
                self.db.log_event(
                    'subscription_created', 
                    gateway_sub_id, 
                    user_id, 
                    gateway_response,
                    provider=gateway,
                    processed=True
                )
                
                conn.commit()
                
                result = {
                    'id': subscription_id,
                    'razorpay_subscription_id': razorpay_subscription_id,
                    'paypal_subscription_id': paypal_subscription_id,
                    'status': 'created',
                    'short_url': gateway_response.get('short_url'),
                    'user_id': user_id,
                    'plan_id': plan_id,
                    'app_id': app_id,
                    'gateway': gateway
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
            logger.error(f"Error saving paid subscription: {str(e)}")
            raise
    
    def _get_plan_for_app(self, plan_id, app_id):
        """Get plan details for specific app with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS}
                WHERE id = %s AND app_id = %s
            """, (plan_id, app_id))
            
            plan = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return plan
            
        except Exception as e:
            logger.error(f"Error getting plan for app: {str(e)}")
            raise

    def _save_paypal_subscription_transaction(self, user_id, plan_id, paypal_subscription_id, app_id, plan, existing):
        """Save PayPal subscription with focused transaction"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            
            
            try:
                # Calculate subscription period
                start_date = datetime.now()
                period_end = calculate_period_end(start_date, plan['interval'], plan['interval_count'])
                
                if existing:
                    # Update existing subscription
                    cursor.execute(f"""
                        UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                        SET plan_id = %s,
                            paypal_subscription_id = %s,
                            status = 'active',
                            current_period_start = %s,
                            current_period_end = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (plan_id, paypal_subscription_id, start_date, period_end, existing['id']))
                    
                    subscription_id = existing['id']
                else:
                    # Create new subscription record
                    subscription_id = generate_id('sub_')
                    
                    cursor.execute(f"""
                        INSERT INTO {DB_TABLE_USER_SUBSCRIPTIONS}
                        (id, user_id, plan_id, paypal_subscription_id, status, 
                        current_period_start, current_period_end, app_id)
                        VALUES (%s, %s, %s, %s, 'active', %s, %s, %s)
                    """, (subscription_id, user_id, plan_id, paypal_subscription_id, 
                        start_date, period_end, app_id))
                
                conn.commit()
                cursor.close()
                conn.close()
                
                return subscription_id
                
            except Exception as e:
                conn.rollback()
                cursor.close()
                conn.close()
                raise
                
        except Exception as e:
            logger.error(f"Error saving PayPal subscription: {str(e)}")
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
    
    def handle_webhook(self, payload, provider='razorpay'):
        """
        Handle webhook events for subscription updates
        
        Args:
            payload: The webhook payload
            provider: The payment provider
            
        Returns:
            dict: Processing result
        """
        try:
            event_type = payload.get('event')
            
            if not event_type:
                logger.error("Invalid webhook payload - no event type")
                return {'status': 'error', 'message': 'Invalid webhook payload'}
            
            
            # Extract entity and user IDs
            entity_id, user_id = self._extract_webhook_ids(payload, provider)
            
            # Log the webhook event
            self.db.log_event(
                event_type,
                entity_id,
                user_id,
                payload,
                provider=provider,
                processed=False
            )
            
            # Route to the appropriate handler based on the event type
            if provider == 'razorpay':
                result = self._handle_razorpay_webhook(event_type, payload)
            elif provider == 'paypal':
                result = {'status': 'ignored', 'message': 'PayPal webhook handling not implemented'}
            else:
                logger.error(f"Unknown provider: {provider}")
                result = {'status': 'error', 'message': f'Unknown provider: {provider}'}
            
            # Update the event log to mark as processed
            self.db.log_event(
                f"{event_type}_processed",
                entity_id,
                user_id,
                result,
                provider=provider,
                processed=True
            )
            
            return {'status': 'success', 'message': f'Processed {event_type} event', 'result': result}
                
        except Exception as e:
            logger.error(f"Error handling webhook: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _extract_webhook_ids(self, payload, provider):
        """Extract entity ID and user ID from webhook payload"""
        entity_id = None
        user_id = None
        
        if provider == 'razorpay':
            if 'payload' in payload:
                if 'payment' in payload['payload']:
                    entity_id = payload['payload']['payment'].get('id')
                elif 'subscription' in payload['payload']:
                    entity_id = payload['payload']['subscription'].get('id')
                    # Try to extract user_id from notes
                    if 'notes' in payload['payload']['subscription']:
                        user_id = payload['payload']['subscription']['notes'].get('user_id')
        
        return entity_id, user_id

    def _handle_razorpay_webhook(self, event_type, payload):
        """Handle Razorpay webhook events"""
        if event_type == 'subscription.authenticated':
            return self._handle_razorpay_subscription_authenticated(payload)
        elif event_type == 'subscription.activated':
            return self._handle_razorpay_subscription_activated(payload)
        elif event_type == 'subscription.charged':
            return self._handle_razorpay_subscription_charged(payload)
        elif event_type == 'subscription.completed':
            return self._handle_razorpay_subscription_completed(payload)
        elif event_type == 'subscription.cancelled':
            return self._handle_razorpay_subscription_cancelled(payload)
        else:
            return {'status': 'ignored', 'message': f'Unhandled event type: {event_type}'}

    def _handle_other_payment_upgrade_with_refund(self, subscription, current_plan, new_plan, app_id, value_remaining_amount):
        """Handle other payment methods (NetBanking, etc.) with refund flow"""
        logger.info(f"[UPGRADE] Handling other payment method upgrade with refund")
        
        try:
            # Create data for cancel and recreate flow
            subscription_id = subscription['id']
            user_id = subscription['user_id']
            
            # Execute refund-based upgrade
            return self._execute_cancel_and_recreate_with_refund(
                user_id=user_id,
                subscription_id=subscription_id,
                current_plan=current_plan,
                new_plan=new_plan,
                app_id=app_id,
                refund_amount=value_remaining_amount,
                payment_method='other'
            )
        except Exception as e:
            logger.error(f"[UPGRADE] Error in other payment upgrade with refund: {str(e)}")
            raise        

    def _handle_card_upgrade_with_discount(self, subscription, current_plan, new_plan, app_id, 
                                        discount_offer_pct, discount_amount, value_remaining_pct):
        """Handle card payment method upgrade with discount offer"""
        logger.info(f"[UPGRADE] Handling Card upgrade with {discount_offer_pct}% discount")
        
        try:
            # Create data for cancel and recreate flow
            subscription_id = subscription['id']
            user_id = subscription['user_id']
            
            # Standard cancel and recreate flow with discount
            return self._execute_cancel_and_recreate_with_discount(
                user_id=user_id,
                subscription_id=subscription_id,
                current_plan=current_plan,
                new_plan=new_plan,
                app_id=app_id,
                discount_pct=discount_offer_pct,
                discount_amount=discount_amount,
                payment_method='card',
                value_remaining_pct=value_remaining_pct
            )
        except Exception as e:
            logger.error(f"[UPGRADE] Error in Card upgrade with discount: {str(e)}")
            raise


    def _handle_upi_upgrade_with_discount(self, subscription, current_plan, new_plan, app_id, 
                                      discount_offer_pct, discount_amount, value_remaining_pct):
        """Handle UPI payment method upgrade with discount offer"""
        logger.info(f"[UPGRADE] Handling UPI upgrade with {discount_offer_pct}% discount")
        
        try:
            # Create data for cancel and recreate flow
            subscription_id = subscription['id']
            user_id = subscription['user_id']
            
            # Standard cancel and recreate flow with discount
            return self._execute_cancel_and_recreate_with_discount(
                user_id=user_id,
                subscription_id=subscription_id,
                current_plan=current_plan,
                new_plan=new_plan,
                app_id=app_id,
                discount_pct=discount_offer_pct,
                discount_amount=discount_amount,
                payment_method='upi',
                value_remaining_pct=value_remaining_pct
            )
        except Exception as e:
            logger.error(f"[UPGRADE] Error in UPI upgrade with discount: {str(e)}")
            raise
    
    def _handle_razorpay_subscription_authenticated(self, payload):
        """Handle subscription.authenticated webhook event"""
        try:
            subscription_data = self._extract_subscription_data(payload)
            razorpay_subscription_id = subscription_data.get('id')
          
            if not razorpay_subscription_id:
                logger.error("No subscription ID in authenticated webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            self._update_subscription_status(
                razorpay_subscription_id, 
                'authenticated', 
                subscription_data,
                condition="AND status != 'active'"
            )
            
            logger.info(f"Subscription authenticated: {razorpay_subscription_id}")
            return {'status': 'success', 'message': 'Subscription authenticated'}
            
        except Exception as e:
            logger.error(f"Error handling subscription authenticated: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _extract_subscription_data(self, payload):
        """Extract subscription data from webhook payload"""
        return payload.get('payload', {}).get('subscription', {}).get('entity', {})

    def _get_subscription_by_razorpay_id(self, razorpay_subscription_id):
        """Get subscription by Razorpay ID with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT id, user_id, plan_id, app_id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s
            """, (razorpay_subscription_id,))
            
            subscription = cursor.fetchone()
            
            cursor.close()
            conn.close()
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting subscription by Razorpay ID: {str(e)}")
            raise

    def _update_subscription_status(self, razorpay_subscription_id, status, subscription_data, condition=""):
        """Update subscription status with isolated connection"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = %s, 
                    updated_at = NOW(),
                    metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
                WHERE razorpay_subscription_id = %s
                {condition}
            """, (status, json.dumps(subscription_data), razorpay_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating subscription status: {str(e)}")
            raise
    
    def _handle_razorpay_subscription_activated(self, payload):
        """Handle subscription.activated webhook event"""
        try:
            subscription_data = self._extract_subscription_data(payload)
            razorpay_subscription_id = subscription_data.get('id')
            
            logger.info(f"Subscription Activated - Subscription ID: {razorpay_subscription_id}")
            
            if not razorpay_subscription_id:
                logger.error("No subscription ID in activated webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Calculate period dates
            start_date, period_end = self._calculate_subscription_period(subscription_data, subscription['plan_id'])
            
            # Update subscription
            self._activate_subscription_with_period(razorpay_subscription_id, start_date, period_end, subscription_data)
            
            
            # Initialize resource quota separately
            quota_result = self.initialize_resource_quota(subscription['user_id'], subscription['id'], subscription['app_id'])
            
            if not quota_result:
                logger.error(f"Failed to initialize resource quota for subscription {subscription['id']}")

            return {
                'status': 'success', 
                'message': 'Subscription activated',
                'period_start': start_date.isoformat(),
                'period_end': period_end.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling subscription activated: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _calculate_subscription_period(self, subscription_data, plan_id):
        """Calculate subscription period dates"""
        start_date = datetime.now()
        
        # Try to get start date from payload
        start_at = subscription_data.get('start_at')
        if start_at:
            try:
                start_timestamp = int(start_at)
                start_date = datetime.fromtimestamp(start_timestamp)
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

    def _get_plan_interval_details(self, plan_id):
       """Get plan interval details with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT interval, interval_count
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

    def _activate_subscription_with_period(self, razorpay_subscription_id, start_date, period_end, subscription_data):
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
           """, (start_date, period_end, json.dumps(subscription_data), razorpay_subscription_id))
           
           conn.commit()
           cursor.close()
           conn.close()
           
       except Exception as e:
           logger.error(f"Error activating subscription with period: {str(e)}")
           raise
   
    def _handle_razorpay_subscription_charged(self, subscription_data, payment_data):
        """Handle subscription charged event with plan change and payment method detection"""
        try:
            razorpay_sub_id = subscription_data.get('id')
            webhook_plan_id = subscription_data.get('plan_id')  # Plan ID from webhook
            
            # Get current subscription from database
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT id, user_id, plan_id, app_id, razorpay_subscription_id
                FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s AND status = 'active'
            """, (razorpay_sub_id,))
            
            subscription = cursor.fetchone()
            if not subscription:
                logger.warning(f"Active subscription not found for Razorpay ID: {razorpay_sub_id}")
                cursor.close()
                conn.close()
                return {'success': False, 'error': 'Subscription not found'}
            
            database_plan_id = subscription['plan_id']
            
            # DETECT PLAN CHANGE (Manual Downgrade/Upgrade)
            if webhook_plan_id and webhook_plan_id != database_plan_id:
                logger.info(f"Plan change detected: {database_plan_id} → {webhook_plan_id}")
                
                # Get new plan details
                new_plan = self._get_plan_by_razorpay_id(webhook_plan_id, subscription['app_id'])
                if new_plan:
                    # Update subscription plan in database
                    cursor.execute(f"""
                        UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                        SET plan_id = %s, plan_name = %s, amount = %s, currency = %s
                        WHERE id = %s
                    """, (new_plan['id'], new_plan['name'], new_plan['amount'], 
                        new_plan['currency'], subscription['id']))
                    
                    # Reset resource quota to new plan
                    self._reset_quota_for_plan_change(
                        subscription['user_id'], 
                        subscription['id'],
                        new_plan,
                        subscription['app_id']
                    )
                    
                    self._log_subscription_event(
                        subscription['user_id'],
                        subscription['id'],
                        'payment_method_changed',
                        {
                            'old_method': last_payment_method,
                            'new_method': current_payment_method,
                            'change_detected_in': 'subscription_charged_webhook'
                        },
                        'razorpay'  # Add provider
                    )
                    
                    logger.info(f"Plan change synced: User {subscription['user_id']} moved to {new_plan['name']}")
            
            # DETECT PAYMENT METHOD CHANGE
            current_payment_method = payment_data.get('method')  # From current payment
            
            if current_payment_method:
                # Get last stored payment method
                cursor.execute("""
                    SELECT payment_method FROM subscription_invoices 
                    WHERE subscription_id = %s 
                    ORDER BY created_at DESC LIMIT 1
                """, (subscription['id'],))
                
                last_method_record = cursor.fetchone()
                last_payment_method = last_method_record['payment_method'] if last_method_record else None
                
                # Check if payment method changed
                if last_payment_method and current_payment_method != last_payment_method:
                    logger.info(f"Payment method changed: {last_payment_method} → {current_payment_method}")
                    
                    # Log payment method change
                    self._log_subscription_event(
                        subscription['user_id'],
                        subscription['id'],
                        'payment_method_changed',
                        {
                            'old_method': last_payment_method,
                            'new_method': current_payment_method,
                            'change_detected_in': 'subscription_charged_webhook'
                        }
                    )
            
            # Create invoice record for this payment
            invoice_id = generate_id('inv_')
            payment_id = payment_data.get('id')
            amount = payment_data.get('amount', 0) / 100  # Convert paisa to rupees
            currency = payment_data.get('currency', 'INR')
            
            cursor.execute("""
                INSERT INTO subscription_invoices 
                (id, subscription_id, user_id, razorpay_payment_id, amount, currency, 
                status, payment_method, invoice_date, app_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            """, (
                invoice_id,
                subscription['id'],
                subscription['user_id'],
                payment_id,
                amount,
                currency,
                'paid',
                current_payment_method or 'unknown',
                subscription['app_id']
            ))
            
            # Reset resource quota for the new billing period
            self.initialize_resource_quota(
                subscription['user_id'], 
                subscription['id'], 
                subscription['app_id']
            )
            
            # Update subscription billing dates
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET current_period_start = NOW(),
                    current_period_end = DATE_ADD(NOW(), INTERVAL 1 WEEK),
                    status = 'active'
                WHERE id = %s
            """, (subscription['id'],))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Subscription charged processed: {subscription['user_id']} - ₹{amount}")
            
            return {
                'success': True,
                'subscription_id': subscription['id'],
                'invoice_id': invoice_id,
                'amount': amount,
                'payment_method': current_payment_method
            }
            
        except Exception as e:
            logger.error(f"Error handling subscription charged with plan sync: {str(e)}")
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()
            raise

    def _get_plan_by_razorpay_id(self, razorpay_plan_id, app_id):
        """Get plan details by Razorpay plan ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT * FROM subscription_plans 
                WHERE razorpay_plan_id = %s AND app_id = %s AND is_active = 1
            """, (razorpay_plan_id, app_id))
            
            plan = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if plan:
                logger.info(f"Found plan by Razorpay ID {razorpay_plan_id}: {plan['name']}")
            else:
                logger.warning(f"No plan found for Razorpay ID: {razorpay_plan_id}")
            
            return plan
            
        except Exception as e:
            logger.error(f"Error getting plan by Razorpay ID {razorpay_plan_id}: {str(e)}")
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()
            return None

    def _reset_quota_for_plan_change(self, user_id, subscription_id, new_plan, app_id):
        """Reset resource quota when plan changes manually"""
        try:
            # Get new plan features
            new_features = json.loads(new_plan.get('features', '{}'))
            
            # Update resource quota to new plan limits
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # First, delete existing quota records for this user and app
            cursor.execute(f"""
                DELETE FROM {DB_TABLE_RESOURCE_USAGE}
                WHERE user_id = %s AND app_id = %s
            """, (user_id, app_id))
            
            # Create new quota records based on new plan
            for resource_type, limit in new_features.items():
                quota_id = generate_id('quota_')
                cursor.execute(f"""
                    INSERT INTO {DB_TABLE_RESOURCE_USAGE}
                    (id, user_id, subscription_id, app_id, resource_type, quota_limit, 
                    current_usage, billing_period_start, billing_period_end)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), DATE_ADD(NOW(), INTERVAL 1 WEEK))
                """, (
                    quota_id,
                    user_id,
                    subscription_id,
                    app_id,
                    resource_type,
                    limit,
                    0  # Reset usage to 0
                ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Resource quota reset for plan change: {user_id} → {new_plan['name']}")
            logger.info(f"New quota limits: {new_features}")
            
        except Exception as e:
            logger.error(f"Error resetting quota for plan change: {str(e)}")
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()
            raise

    def _get_latest_payment_method(self, subscription_id):
        """Get the most recent payment method for a subscription"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT payment_method, created_at FROM subscription_invoices 
                WHERE subscription_id = %s 
                ORDER BY created_at DESC LIMIT 1
            """, (subscription_id,))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result:
                logger.info(f"Latest payment method for subscription {subscription_id}: {result['payment_method']}")
                return result['payment_method']
            else:
                logger.warning(f"No payment method found for subscription {subscription_id}")
                return 'unknown'
            
        except Exception as e:
            logger.error(f"Error getting latest payment method for subscription {subscription_id}: {str(e)}")
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()
            return 'unknown'

    def _log_subscription_event(self, user_id, subscription_id, event_type, event_data=None, provider='system'):
        """Log subscription events for audit trail"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            event_id = generate_id('event_')
            
            cursor.execute("""
                INSERT INTO subscription_events_log 
                (id, user_id, subscription_id, event_type, event_data, provider, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """, (
                event_id,
                user_id,
                subscription_id,
                event_type,
                json.dumps(event_data) if event_data else None,
                provider
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Subscription event logged: {event_type} for user {user_id}")
            
        except Exception as e:
            logger.error(f"Error logging subscription event: {str(e)}")
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()

    def _extract_charged_subscription_data(self, payload):
       """Extract subscription data from charged webhook payload"""
       subscription_data = payload.get('payload', {}).get('subscription', {})
       
       # Check if subscription data is nested inside an "entity" field
       if 'entity' in subscription_data:
           subscription_data = subscription_data.get('entity', {})
       
       return subscription_data

    def _renew_subscription_with_invoice(self, razorpay_subscription_id, new_start, new_end, subscription_data, subscription, razorpay_invoice_id, razorpay_payment_id, payment_data):
        """Renew subscription and record invoice in transaction"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            try:
                # Update subscription status
                cursor.execute(f"""
                    UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                    SET status = 'active',
                        current_period_start = %s,
                        current_period_end = %s,
                        updated_at = NOW(),
                        metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
                    WHERE razorpay_subscription_id = %s
                """, (new_start, new_end, json.dumps({
                    'subscription': subscription_data
                }), razorpay_subscription_id))
                
                # If we have invoice details, record the invoice WITH payment method
                if razorpay_invoice_id:
                    invoice_id = generate_id('inv_')
                    
                    # Get invoice amount and payment method
                    amount = payment_data.get('amount', 0)
                    currency = payment_data.get('currency', 'INR')
                    status = payment_data.get('status', 'pending')
                    payment_method = payment_data.get('method')  # ← NEW: Extract payment method
                    
                    if status == 'captured':
                        status = 'Paid'
                    
                    # Insert invoice record with payment method
                    cursor.execute(f"""
                        INSERT INTO subscription_invoices
                        (id, subscription_id, user_id, razorpay_invoice_id, 
                        amount, currency, payment_method, status, payment_id, invoice_date, app_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                    """, (invoice_id, subscription['id'], subscription['user_id'], 
                        razorpay_invoice_id, amount, currency, payment_method, status,  # ← NEW: Include payment_method
                        razorpay_payment_id, subscription['app_id']))
                
                conn.commit()
                cursor.close()
                conn.close()
                
            except Exception as e:
                conn.rollback()
                cursor.close()
                conn.close()
                raise
                
        except Exception as e:
            logger.error(f"Error renewing subscription with invoice: {str(e)}")
            raise
   
    def _handle_razorpay_subscription_completed(self, payload):
       """Handle subscription.completed webhook event"""
       try:
           subscription_data = self._extract_subscription_data(payload)
           razorpay_subscription_id = subscription_data.get('id')
           
           if not razorpay_subscription_id:
               logger.error("No subscription ID in completed webhook")
               return {'status': 'error', 'message': 'Missing subscription ID'}
           
           subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
           
           if not subscription:
               logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
               return {'status': 'error', 'message': 'Subscription not found'}
           
           self._update_subscription_status(razorpay_subscription_id, 'completed', subscription_data)
           
           logger.debug(f"Subscription completed: {razorpay_subscription_id}")
           return {'status': 'success', 'message': 'Subscription marked as completed'}
           
       except Exception as e:
           logger.error(f"Error handling subscription completed: {str(e)}")
           logger.error(traceback.format_exc())
           return {'status': 'error', 'message': str(e)}
   
    def _handle_razorpay_subscription_cancelled(self, payload):
       """Handle subscription.cancelled webhook event"""
       try:
           subscription_data = self._extract_subscription_data(payload)
           razorpay_subscription_id = subscription_data.get('id')
           
           if not razorpay_subscription_id:
               logger.error("No subscription ID in cancelled webhook")
               return {'status': 'error', 'message': 'Missing subscription ID'}
           
           subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
           
           if not subscription:
               logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
               return {'status': 'error', 'message': 'Subscription not found'}
           
           self._update_subscription_status(razorpay_subscription_id, 'cancelled', subscription_data)
           
           logger.info(f"Subscription cancelled: {razorpay_subscription_id}")
           return {'status': 'success', 'message': 'Subscription marked as cancelled'}
           
       except Exception as e:
           logger.error(f"Error handling subscription cancelled: {str(e)}")
           logger.error(traceback.format_exc())
           return {'status': 'error', 'message': str(e)}
   
    def cancel_subscription(self, user_id, subscription_id):
       """
       Cancel a user's subscription at the end of the billing cycle,
       but keep it active until that date
       
       Args:
           user_id: The user's ID
           subscription_id: The subscription ID
           
       Returns:
           dict: Cancellation result
       """
       
       try:
           # Phase 1: Get subscription data
           subscription = self._get_subscription_for_cancellation(user_id, subscription_id)
           
           # Phase 2: Cancel with gateway (no DB connection open)
           self._cancel_with_gateway(subscription)
           
           # Phase 3: Update database
           return self._mark_subscription_cancelled(subscription_id, subscription)
           
       except Exception as e:
           logger.error(f"Error scheduling subscription cancellation: {str(e)}")
           logger.error(traceback.format_exc())
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

    def _cancel_with_gateway(self, subscription):
       """Cancel subscription with payment gateway"""
       if subscription.get('razorpay_subscription_id'):
           try:
               # Use the Razorpay provider to cancel
               result = self.razorpay.cancel_subscription(
                   subscription['razorpay_subscription_id'],
                   cancel_at_cycle_end=True
               )
               
               if result.get('error'):
                   logger.error(f"Error scheduling cancellation with Razorpay: {result.get('message')}")
                   # Continue with local cancellation even if Razorpay fails
               else:
                   logger.info(f"Razorpay subscription scheduled for cancellation: {subscription['razorpay_subscription_id']}")
                   
           except Exception as e:
               logger.error(f"Error scheduling cancellation with Razorpay: {str(e)}")
               # Continue with local cancellation even if Razorpay fails

    def _mark_subscription_cancelled(self, subscription_id, subscription):
       """Mark subscription as cancelled in database"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           # Convert datetime to string to avoid JSON serialization issues
           current_time_str = datetime.now().isoformat()
           
           # Update subscription metadata to indicate it's scheduled for cancellation,
           # but keep status as "active"
           cursor.execute(f"""
               UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
               SET metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s), 
                   updated_at = NOW()
               WHERE id = %s
           """, (json.dumps({
               'cancellation_scheduled': True,
               'cancelled_at': current_time_str,
           }), subscription_id))
           
           conn.commit()
           cursor.close()
           conn.close()
           
           # Format end date for JSON if it exists
           end_date_str = None
           if subscription.get('current_period_end'):
               if isinstance(subscription['current_period_end'], datetime):
                   end_date_str = subscription['current_period_end'].isoformat()
               else:
                   end_date_str = str(subscription['current_period_end'])
           
           # Return the updated subscription data
           return {
               "id": subscription_id,
               "status": "active",  # Status remains active
               "cancellation_scheduled": True,  # Add this flag instead
               "end_date": end_date_str,
               "message": "Subscription will remain active until the end of the current billing period"
           }
           
       except Exception as e:
           logger.error(f"Error marking subscription cancelled: {str(e)}")
           raise
      
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
   
    def activate_subscription(self, user_id, subscription_id, payment_id=None):
       """
       Manually activate a subscription (used for verification endpoints)
       
       Args:
           user_id: The user's ID
           subscription_id: The Razorpay subscription ID
           payment_id: Optional payment ID
           
       Returns:
           dict: Activation result
       """
       
       try:
           subscription = self._get_subscription_by_razorpay_id(subscription_id)
           if not subscription:
               return {'status': 'error', 'message': 'Subscription not found'}
           
           plan = self._get_plan(subscription['plan_id'])
           if not plan:
               return {'status': 'error', 'message': 'Plan not found'}
           
           return self._activate_subscription_transaction(subscription, plan, payment_id)
           
       except Exception as e:
           logger.error(f"Error manually activating subscription: {str(e)}")
           logger.error(traceback.format_exc())
           return {'status': 'error', 'message': str(e)}

    def _activate_subscription_transaction(self, subscription, plan, payment_id):
       """Activate subscription in transaction"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           
           
           try:
               # Calculate subscription period
               start_date = datetime.now()
               period_end = calculate_period_end(start_date, plan['interval'], plan['interval_count'])
               
               # Update subscription status
               cursor.execute(f"""
                   UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                   SET status = 'active', 
                       current_period_start = %s,
                       current_period_end = %s,
                       updated_at = NOW()
                   WHERE razorpay_subscription_id = %s
               """, (start_date, period_end, subscription['razorpay_subscription_id']))
               
               # Record payment if provided
               if payment_id:
                   invoice_id = generate_id('inv_')
                   
                   cursor.execute("""
                       INSERT INTO subscription_invoices
                       (id, subscription_id, user_id, razorpay_invoice_id, amount, status, payment_id, app_id, invoice_date, paid_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                   """, (
                       invoice_id, 
                       subscription['id'],
                       subscription['user_id'],
                       'manual_activation',
                       plan['amount'],
                       'paid',
                       payment_id,
                       subscription['app_id']
                   ))
               
               # Log the manual activation
               self.db.log_event(
                   'manual_activation',
                   subscription['razorpay_subscription_id'],
                   subscription['user_id'],
                   {'payment_id': payment_id},
                   provider='razorpay',
                   processed=True
               )
               
               conn.commit()
               cursor.close()
               conn.close()
               
               logger.info(f"Subscription {subscription['razorpay_subscription_id']} manually activated")
               return {'status': 'success', 'message': 'Subscription activated'}
               
           except Exception as e:
               conn.rollback()
               cursor.close()
               conn.close()
               raise
               
       except Exception as e:
           logger.error(f"Error in activation transaction: {str(e)}")
           raise
       
   # service.py - Updated resource tracking mechanism

    def get_resource_limits(self, user_id, app_id):
       """
       Get resource limits for a user based on their subscription plan
       
       Args:
           user_id: The user's ID
           app_id: The application ID
           
       Returns:
           dict: Resource limits
       """
       
       try:
           # Get the user's active subscription
           subscription = self.get_user_subscription(user_id, app_id)
           
           if not subscription:
               # Default limits for free plan
               if app_id == 'marketfit':
                   return {
                       'document_pages': 50,
                       'perplexity_requests': 20
                   }
               else:  # saleswit
                   return {
                       'requests': 20  # SalesWit only has request parameter
                   }
           
           # Get limits from subscription features
           features = subscription.get('features', {})
           if isinstance(features, str):
               import json
               features = json.loads(features)
           
           # Return appropriate limits based on app
           if app_id == 'marketfit':
               return {
                   'document_pages': features.get('document_pages', 50),
                   'perplexity_requests': features.get('perplexity_requests', 20)
               }
           else:  # saleswit
               return {
                   'requests': features.get('requests', 20)  # SalesWit only has request parameter
               }
               
       except Exception as e:
           logger.error(f"Error getting resource limits: {str(e)}")
           logger.error(traceback.format_exc())
           # Return default limits on error
           if app_id == 'marketfit':
               return {
                   'document_pages': 50,
                   'perplexity_requests': 20
               }
           else:  # saleswit
               return {
                   'requests': 20  # SalesWit only has request parameter
               }

   # service.py - Updated initialize_resource_quota function

    def initialize_resource_quota(self, user_id, subscription_id, app_id):
       """
       Initialize or reset resource quota for a subscription period
       
       Args:
           user_id: The user's ID
           subscription_id: The subscription ID
           app_id: The application ID
           
       Returns:
           bool: Success status
       """
       
       try:
           subscription_details = self._get_subscription_with_features(subscription_id)
           
           if not subscription_details:
               logger.error(f"Subscription {subscription_id} not found")
               return False
           
           # Parse features
           features = self._parse_subscription_features(subscription_details.get('features', '{}'))
           
           # Set quota based on app
           quota_values = self._calculate_quota_values(app_id, features)
           
           # Create or update quota record
           return self._save_quota_record(user_id, subscription_id, app_id, subscription_details, quota_values)
           
       except Exception as e:
           logger.error(f"Error initializing resource quota: {str(e)}")
           logger.error(traceback.format_exc())
           return False

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
               'document_pages_quota': features.get('document_pages', 50),
               'perplexity_requests_quota': features.get('perplexity_requests', 20),
               'requests_quota': 0
           }
       else:  # saleswit
           return {
               'document_pages_quota': 0,
               'perplexity_requests_quota': 0,
               'requests_quota': features.get('requests', 20)
           }

    def _save_quota_record(self, user_id, subscription_id, app_id, subscription_details, quota_values):
       """Save or update quota record"""
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
                       updated_at = NOW()
                   WHERE id = %s
               """, (
                   quota_values['document_pages_quota'],
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
                   document_pages_quota, perplexity_requests_quota, requests_quota)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               """, (
                   user_id,
                   subscription_id,
                   app_id,
                   subscription_details.get('current_period_start') or datetime.now(),
                   subscription_details.get('current_period_end') or (datetime.now() + timedelta(days=30)),
                   quota_values['document_pages_quota'],
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

    def reset_quota_on_renewal(self, subscription_id):
       """
       Reset resource quota when a subscription is renewed
       
       Args:
           subscription_id: The subscription ID
           
       Returns:
           bool: Success status
       """
       
       try:
           subscription_details = self._get_subscription_with_features(subscription_id)
           
           if not subscription_details:
               logger.error(f"Subscription {subscription_id} not found")
               return False
           
           # Parse features and calculate quota values
           features = self._parse_subscription_features(subscription_details.get('features', '{}'))
           quota_values = self._calculate_quota_values(subscription_details.get('app_id'), features)
           
           # Reset quota record
           return self._reset_quota_record(subscription_details, quota_values)
           
       except Exception as e:
           logger.error(f"Error resetting quota on renewal: {str(e)}")
           logger.error(traceback.format_exc())
           return False

    def _reset_quota_record(self, subscription_details, quota_values):
       """Reset quota record for renewal"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           # Insert or update quota record for the new billing period
           cursor.execute(f"""
               INSERT INTO {DB_TABLE_RESOURCE_USAGE}
               (user_id, subscription_id, app_id, billing_period_start, billing_period_end,
               document_pages_quota, perplexity_requests_quota, requests_quota)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
               document_pages_quota = %s,
               perplexity_requests_quota = %s,
               requests_quota = %s
           """, (
               subscription_details['user_id'],
               subscription_details['id'],
               subscription_details['app_id'],
               subscription_details['current_period_start'],
               subscription_details['current_period_end'],
               quota_values['document_pages_quota'],
               quota_values['perplexity_requests_quota'],
               quota_values['requests_quota'],
               quota_values['document_pages_quota'],
               quota_values['perplexity_requests_quota'],
               quota_values['requests_quota']
           ))
           
           conn.commit()
           cursor.close()
           conn.close()
           
           return True
           
       except Exception as e:
           logger.error(f"Error resetting quota record: {str(e)}")
           raise

    def get_subscription_by_gateway_id(self, gateway_sub_id, provider):
       """
       Get a subscription by its payment gateway subscription ID
       
       Args:
           gateway_sub_id: The gateway subscription ID
           provider: The payment gateway provider ('razorpay' or 'paypal')
           
       Returns:
           dict: Subscription details or None if not found
       """
       logger.info(f"Getting subscription by {provider} ID: {gateway_sub_id}")
       
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           # Different column name based on provider
           if provider == 'razorpay':
               id_column = 'razorpay_subscription_id'
           elif provider == 'paypal':
               id_column = 'paypal_subscription_id'
           else:
               logger.error(f"Unknown payment provider: {provider}")
               cursor.close()
               conn.close()
               return None
           
           # Get the subscription with the gateway ID
           cursor.execute(f"""
               SELECT us.*, sp.name as plan_name, sp.features, sp.amount, sp.currency, sp.interval 
               FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
               JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
               WHERE us.{id_column} = %s
               ORDER BY us.updated_at DESC LIMIT 1
           """, (gateway_sub_id,))
           
           subscription = cursor.fetchone()
           
           cursor.close()
           conn.close()
           
           # Parse JSON fields
           if subscription:
               subscription = self._parse_subscription_json_fields(subscription)
           
           return subscription
           
       except Exception as e:
           logger.error(f"Error getting subscription by gateway ID: {str(e)}")
           logger.error(traceback.format_exc())
           return None

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
    
    def _get_free_plan(self, app_id):
       """Get free plan with isolated connection"""
       try:
           conn = self.db.get_connection()
           cursor = conn.cursor(dictionary=True)
           
           cursor.execute(f"""
               SELECT id FROM {DB_TABLE_SUBSCRIPTION_PLANS}
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
    
       

    # NEW WEBHOOK HANDLERS FOR MISSING RAZORPAY EVENTS

    def _handle_razorpay_subscription_pending(self, payload):
        """Handle subscription.pending webhook event"""
        try:
            subscription_data = self._extract_subscription_data(payload)
            razorpay_subscription_id = subscription_data.get('id')
            
            if not razorpay_subscription_id:
                logger.error("No subscription ID in pending webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            self._update_subscription_status(razorpay_subscription_id, 'pending', subscription_data)
            
            # Log the pending status
            self.db.log_subscription_action(
                subscription['id'],
                'payment_pending',
                {'razorpay_subscription_id': razorpay_subscription_id, 'event_data': subscription_data},
                'razorpay_webhook'
            )
            
            logger.info(f"Subscription marked as pending: {razorpay_subscription_id}")
            return {'status': 'success', 'message': 'Subscription marked as pending'}
            
        except Exception as e:
            logger.error(f"Error handling subscription pending: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _handle_razorpay_subscription_halted(self, payload):
        """Handle subscription.halted webhook event"""
        try:
            subscription_data = self._extract_subscription_data(payload)
            razorpay_subscription_id = subscription_data.get('id')
            
            if not razorpay_subscription_id:
                logger.error("No subscription ID in halted webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            self._update_subscription_status(razorpay_subscription_id, 'halted', subscription_data)
            
            # Log the halted status
            self.db.log_subscription_action(
                subscription['id'],
                'payment_halted',
                {'razorpay_subscription_id': razorpay_subscription_id, 'event_data': subscription_data},
                'razorpay_webhook'
            )
            
            logger.info(f"Subscription marked as halted: {razorpay_subscription_id}")
            return {'status': 'success', 'message': 'Subscription marked as halted'}
            
        except Exception as e:
            logger.error(f"Error handling subscription halted: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _handle_razorpay_subscription_updated(self, payload):
        """Handle subscription.updated webhook event"""
        try:
            subscription_data = self._extract_subscription_data(payload)
            razorpay_subscription_id = subscription_data.get('id')
            
            if not razorpay_subscription_id:
                logger.error("No subscription ID in updated webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Update subscription with new data
            self._update_subscription_from_webhook(razorpay_subscription_id, subscription_data)
            
            # Log the update
            self.db.log_subscription_action(
                subscription['id'],
                'subscription_updated',
                {'razorpay_subscription_id': razorpay_subscription_id, 'event_data': subscription_data},
                'razorpay_webhook'
            )
            
            logger.info(f"Subscription updated: {razorpay_subscription_id}")
            return {'status': 'success', 'message': 'Subscription updated'}
            
        except Exception as e:
            logger.error(f"Error handling subscription updated: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _update_subscription_from_webhook(self, razorpay_subscription_id, subscription_data):
        """Update subscription details from webhook data"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Extract relevant fields from subscription data
            plan_id = subscription_data.get('plan_id')
            status = subscription_data.get('status')
            
            update_fields = []
            update_values = []
            
            if plan_id:
                update_fields.append("plan_id = %s")
                update_values.append(plan_id)
            
            if status:
                update_fields.append("status = %s")
                update_values.append(status)
            
            if update_fields:
                update_fields.append("updated_at = NOW()")
                update_fields.append("metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{}'), %s)")
                update_values.append(json.dumps(subscription_data))
                update_values.append(razorpay_subscription_id)
                
                query = f"""
                    UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                    SET {', '.join(update_fields)}
                    WHERE razorpay_subscription_id = %s
                """
                
                cursor.execute(query, update_values)
                conn.commit()
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating subscription from webhook: {str(e)}")
            raise

    def _update_subscription_status_by_gateway_id(self, gateway_subscription_id, status, data, provider):
        """Update subscription status by gateway ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            if provider == 'razorpay':
                id_column = 'razorpay_subscription_id'
            elif provider == 'paypal':
                id_column = 'paypal_subscription_id'
            else:
                raise ValueError(f"Unknown provider: {provider}")
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = %s, 
                    updated_at = NOW(),
                    metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
                WHERE {id_column} = %s
            """, (status, json.dumps(data), gateway_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating subscription status by gateway ID: {str(e)}")
            raise

    # UPDATE EXISTING WEBHOOK HANDLER TO INCLUDE NEW EVENTS
    def _handle_razorpay_webhook(self, event_type, payload):
        """Handle Razorpay webhook events"""
        if event_type == 'subscription.authenticated':
            return self._handle_razorpay_subscription_authenticated(payload)
        elif event_type == 'subscription.activated':
            return self._handle_razorpay_subscription_activated(payload)
        elif event_type == 'subscription.charged':
            return self._handle_razorpay_subscription_charged(payload)
        elif event_type == 'subscription.completed':
            return self._handle_razorpay_subscription_completed(payload)
        elif event_type == 'subscription.cancelled':
            return self._handle_razorpay_subscription_cancelled(payload)
        elif event_type == 'subscription.pending':  # NEW
            return self._handle_razorpay_subscription_pending(payload)
        elif event_type == 'subscription.halted':   # NEW
            return self._handle_razorpay_subscription_halted(payload)
        elif event_type == 'subscription.updated':  # NEW
            return self._handle_razorpay_subscription_updated(payload)
        else:
            return {'status': 'ignored', 'message': f'Unhandled event type: {event_type}'}

    # UPDATE EXISTING HANDLE_WEBHOOK METHOD
    def handle_webhook(self, payload, provider='razorpay'):
        """Handle webhook events for subscription updates"""
        try:
            if provider == 'razorpay':
                event_type = payload.get('event')
            elif provider == 'paypal':
                event_type = payload.get('event_type')
            else:
                event_type = payload.get('event')
            
            if not event_type:
                logger.error("Invalid webhook payload - no event type")
                return {'status': 'error', 'message': 'Invalid webhook payload'}
            
            # Extract entity and user IDs
            entity_id, user_id = self._extract_webhook_ids(payload, provider)
            
            # Log the webhook event
            self.db.log_event(
                event_type,
                entity_id,
                user_id,
                payload,
                provider=provider,
                processed=False
            )
            
            # Route to the appropriate handler based on the event type
            if provider == 'razorpay':
                result = self._handle_razorpay_webhook(event_type, payload)
            elif provider == 'paypal':
                # PayPal events are handled in the webhook handler itself
                result = {'status': 'success', 'message': f'PayPal event {event_type} processed'}
            else:
                logger.error(f"Unknown provider: {provider}")
                result = {'status': 'error', 'message': f'Unknown provider: {provider}'}
            
            # Update the event log to mark as processed
            self.db.log_event(
                f"{event_type}_processed",
                entity_id,
                user_id,
                result,
                provider=provider,
                processed=True
            )
            
            return {'status': 'success', 'message': f'Processed {event_type} event', 'result': result}
                
        except Exception as e:
            logger.error(f"Error handling webhook: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    
    def _get_subscription_payment_method(self, subscription):
        """Detect payment method from database records"""
        try:
            subscription_id = subscription['id']
            
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get most recent payment method from invoices
            cursor.execute("""
                SELECT payment_method FROM subscription_invoices
                WHERE subscription_id = %s AND payment_method IS NOT NULL
                ORDER BY invoice_date DESC LIMIT 1
            """, (subscription_id,))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result and result['payment_method']:
                payment_method = result['payment_method']
                logger.info(f"[UPGRADE] Payment method from database: {payment_method}")
                
                # Map to our categories
                if payment_method in ['upi']:
                    return 'upi'
                elif payment_method in ['card']:
                    return 'card'
                else:
                    return 'other'  # netbanking, wallet, emi
            
            # Fallback: assume 'other' if not found
            logger.warning(f"[UPGRADE] No payment method found for subscription {subscription_id}, defaulting to 'other'")
            return 'other'
            
        except Exception as e:
            logger.error(f"Error detecting payment method: {str(e)}")
            return 'other'  # Safe fallback

    def _get_razorpay_offer_id(self, discount_percentage, payment_method):
        """Get Razorpay offer ID based on discount percentage and payment method"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT offer_id FROM razorpay_offers
                WHERE discount_percentage = %s AND payment_method = %s AND status = 'enabled'
                LIMIT 1
            """, (discount_percentage, payment_method))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return result['offer_id'] if result else None
            
        except Exception as e:
            logger.error(f"Error getting offer ID: {str(e)}")
            return None

    def _handle_inr_upgrade_with_payment_method(self, subscription, current_plan, new_plan, app_id, billing_cycle_info, resource_info):
        """Enhanced INR upgrade handler with payment method detection"""
        logger.info("[UPGRADE] Handling INR upgrade with payment method detection")
        
        try:
            # Calculate value remaining and discount
            value_remaining_pct = self._calculate_value_remaining_percentage(billing_cycle_info, resource_info)
            value_remaining_amount = value_remaining_pct * self._ensure_float(current_plan['amount'])
            discount_pct_of_new_plan = (value_remaining_amount / self._ensure_float(new_plan['amount'])) * 100
            
            discount_result = self._get_discount_offer_for_value(discount_pct_of_new_plan)
            
            # FIX: Check if discount_result is a dictionary (error case) or an integer (discount percentage)
            if isinstance(discount_result, dict):
                if discount_result.get('error'):
                    return discount_result
                discount_offer_pct = discount_result.get('discount_pct', 0)
            else:
                # It's an integer from test discount function
                discount_offer_pct = discount_result
                
            discount_amount = (discount_offer_pct / 100) * self._ensure_float(new_plan['amount'])
            
            # Detect payment method
            payment_method = self._get_subscription_payment_method(subscription)
            
            # Different logic based on payment method
            if payment_method == 'upi':
                logger.info("[UPGRADE] UPI payment detected, using discount cancellation flow")
                return self._handle_upi_upgrade_with_discount(
                    subscription, current_plan, new_plan, app_id,
                    discount_offer_pct, discount_amount, value_remaining_pct
                )
            elif payment_method == 'card':
                logger.info("[UPGRADE] Card payment detected, using discount cancellation flow")
                return self._handle_card_upgrade_with_discount(
                    subscription, current_plan, new_plan, app_id,
                    discount_offer_pct, discount_amount, value_remaining_pct
                )
            else:
                logger.info("[UPGRADE] Other payment method detected, using refund flow")
                return self._handle_other_payment_upgrade_with_refund(
                    subscription, current_plan, new_plan, app_id, value_remaining_amount
                )
                
        except Exception as e:
            logger.error(f"Error in INR upgrade with payment method: {str(e)}")
            raise

    def _handle_inr_upgrade_with_discount(self, subscription, current_plan, new_plan, app_id, 
                                        discount_offer_pct, discount_amount, payment_method,
                                        time_remaining, resource_remaining, value_remaining_amount):
        """Handle INR upgrade with discount for UPI/Card"""
        try:
            # Get the appropriate offer ID
            offer_id = self._get_razorpay_offer_id(discount_offer_pct, payment_method)
            if not offer_id:
                logger.warning(f"No offer found for {discount_offer_pct}% {payment_method}, falling back to manual refund")
                return self._handle_inr_upgrade_with_refund(
                    subscription, current_plan, new_plan, app_id,
                    discount_offer_pct, discount_amount, payment_method,
                    time_remaining, resource_remaining, value_remaining_amount
                )
            
            # NEW MESSAGE (use this):
            message = (
                f"Upgrading from {current_plan['name']} to {new_plan['name']}. "
                f"Your current plan has {time_remaining:.0f}% time remaining and {resource_remaining:.0f}% resources remaining. "
                f"Since your payment method is {payment_method.upper()}, we're applying a {discount_offer_pct}% discount "
                f"to account for the remaining value."
            )
            
            # Phase 1: Cancel current subscription
            logger.info("[UPGRADE] Cancelling current subscription for discount flow")
            cancel_result = self._cancel_razorpay_subscription_immediately(subscription)
            
            # Phase 2: Create new subscription with specific offer ID
            logger.info(f"[UPGRADE] Creating new subscription with offer {offer_id}")
            new_subscription_result = self._create_subscription_with_specific_offer(
                subscription['user_id'], 
                new_plan['id'], 
                app_id, 
                offer_id,
                payment_method
            )
            
            # Phase 3: Log the upgrade
            self.db.log_subscription_action(
                subscription['id'],
                'inr_upgrade_with_discount',
                {
                    'old_plan_id': current_plan['id'],
                    'new_plan_id': new_plan['id'],
                    'payment_method': payment_method,
                    'discount_applied_pct': discount_offer_pct,
                    'offer_id_used': offer_id,
                    'cancel_result': cancel_result,
                    'new_subscription': new_subscription_result
                },
                f"user_{subscription['user_id']}"
            )
            
            return {
                'success': True,
                'upgrade_type': 'cancel_and_recreate_with_discount',
                'old_subscription_id': subscription['id'],
                'new_subscription': new_subscription_result,
                'payment_method': payment_method,
                'discount_applied': discount_offer_pct,
                'discount_amount': discount_amount,
                'offer_id_used': offer_id,
                'final_amount': float(new_plan['amount']) - float(discount_amount),
                'message': message,
                'razorpay_link': new_subscription_result.get('short_url'),
                'calculation_details': {
                    'original_price': new_plan['amount'],
                    'discount_percentage': discount_offer_pct,
                    'discount_amount': discount_amount,
                    'final_amount': float(new_plan['amount']) - float(discount_amount),
                    'remaining_value_adjustment': True
                }
            }
            
        except Exception as e:
            logger.error(f"Error in discount flow: {str(e)}")
            raise

    def _handle_inr_upgrade_with_refund(self, subscription, current_plan, new_plan, app_id,
                                    discount_offer_pct, discount_amount, payment_method,
                                    time_remaining, resource_remaining, value_remaining_amount):
        """Handle INR upgrade with manual refund for other payment methods"""
        try:
            # Create detailed message for refund flow
            message = (
                f"Upgrading from {current_plan['name']} to {new_plan['name']}. "
                f"Your current plan has {time_remaining:.0f}% time remaining and {resource_remaining:.0f}% resources remaining. "
                f"Since your payment method is {payment_method.upper()}, you'll pay the full amount (₹{new_plan['amount']}) now "
                f"and receive a refund of ₹{value_remaining_amount:.0f} for your remaining subscription value in 3-5 working days."
            )
            
            # Phase 1: Cancel current subscription
            logger.info("[UPGRADE] Cancelling current subscription for refund flow")
            cancel_result = self._cancel_razorpay_subscription_immediately(subscription)
            
            # Phase 2: Create new subscription without discount
            logger.info("[UPGRADE] Creating new subscription at full price")
            new_subscription_result = self._create_subscription_full_price(
                subscription['user_id'], 
                new_plan['id'], 
                app_id
            )
            
            # Phase 3: Schedule manual refund
            refund_record_id = self._schedule_manual_refund(
                subscription['user_id'],
                subscription['id'],
                value_remaining_amount,
                current_plan,
                payment_method
            )
            
            # Phase 4: Log the upgrade
            self.db.log_subscription_action(
                subscription['id'],
                'inr_upgrade_with_manual_refund',
                {
                    'old_plan_id': current_plan['id'],
                    'new_plan_id': new_plan['id'],
                    'payment_method': payment_method,
                    'refund_amount': value_remaining_amount,
                    'refund_record_id': refund_record_id,
                    'cancel_result': cancel_result,
                    'new_subscription': new_subscription_result
                },
                f"user_{subscription['user_id']}"
            )
            
            return {
                'success': True,
                'upgrade_type': 'cancel_and_recreate_with_refund',
                'old_subscription_id': subscription['id'],
                'new_subscription': new_subscription_result,
                'payment_method': payment_method,
                'refund_amount': value_remaining_amount,
                'refund_record_id': refund_record_id,
                'full_payment_required': True,
                'message': message,
                'razorpay_link': new_subscription_result.get('short_url'),
                'refund_details': {
                    'refund_amount': value_remaining_amount,
                    'processing_time': '3-5 working days',
                    'refund_type': 'manual_processing'
                },
                'calculation_details': {
                    'new_plan_price': new_plan['amount'],
                    'refund_amount': value_remaining_amount,
                    'net_upgrade_cost': new_plan['amount'] - value_remaining_amount
                }
            }
            
        except Exception as e:
            logger.error(f"Error in refund flow: {str(e)}")
            raise

    def _create_subscription_with_specific_offer(self, user_id, plan_id, app_id, offer_id, payment_method):
        """Create subscription with specific Razorpay offer ID"""
        try:
            user = self._get_user_info(user_id)
            if not user:
                raise ValueError("User not found")
            
            plan = self._get_plan(plan_id)
            if not plan:
                raise ValueError("Plan not found")
            
            customer_info = {
                'user_id': user['google_uid'], 
                'email': user.get('email'), 
                'name': user.get('display_name')
            }
            
            additional_notes = {
                'upgrade_with_offer': True,
                'offer_id_used': offer_id,
                'payment_method_detected': payment_method
            }
            
            response = self.razorpay.create_subscription_with_specific_offer(
                plan['razorpay_plan_id'] or plan['id'],
                customer_info,
                app_id,
                offer_id,
                additional_notes
            )
            
            if response.get('error'):
                raise ValueError(response.get('message', 'Failed to create subscription'))
            
            return self._save_paid_subscription(user_id, plan_id, app_id, response)
            
        except Exception as e:
            logger.error(f"Error creating subscription with specific offer: {str(e)}")
            raise

    def _create_subscription_full_price(self, user_id, plan_id, app_id):
        """Create subscription at full price (no discount)"""
        try:
            user = self._get_user_info(user_id)
            if not user:
                raise ValueError("User not found")
            
            plan = self._get_plan(plan_id)
            if not plan:
                raise ValueError("Plan not found")
            
            customer_info = {
                'user_id': user['google_uid'], 
                'email': user.get('email'), 
                'name': user.get('display_name')
            }
            
            additional_notes = {
                'upgrade_full_price': True,
                'manual_refund_scheduled': True
            }
            
            response = self.razorpay.create_subscription(
                plan['razorpay_plan_id'] or plan['id'],
                customer_info,
                app_id,
                additional_notes
            )
            
            if response.get('error'):
                raise ValueError(response.get('message', 'Failed to create subscription'))
            
            return self._save_paid_subscription(user_id, plan_id, app_id, response)
            
        except Exception as e:
            logger.error(f"Error creating full price subscription: {str(e)}")
            raise

    def _schedule_manual_refund(self, user_id, old_subscription_id, refund_amount, current_plan, payment_method):
        """Schedule manual refund for processing"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            refund_id = generate_id('refund_')
            
            cursor.execute("""
                INSERT INTO manual_refunds 
                (id, user_id, subscription_id, refund_amount, currency, 
                original_payment_method, status, reason, scheduled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """, (
                refund_id, user_id, old_subscription_id, refund_amount, 'INR',
                payment_method, 'scheduled', 'subscription_upgrade_refund'
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Scheduled manual refund: {refund_id} for ₹{refund_amount}")
            return refund_id
            
        except Exception as e:
            logger.error(f"Error scheduling manual refund: {str(e)}")
            raise
    
    # PAYPAL SUBSCRIPTION CREATION

    def create_paypal_subscription(self, user_id, plan_id, app_id, customer_info=None):
        """
        Create PayPal subscription using backend API
        
        Args:
            user_id: User's ID (google_uid)
            plan_id: Our internal plan ID
            app_id: Application ID
            customer_info: Optional customer information
            
        Returns:
            dict: Subscription creation result with approval URL
        """
        logger.info(f"Creating PayPal subscription for user {user_id}, plan {plan_id}")
        
        try:
            # Phase 1: Get plan details
            plan = self._get_plan(plan_id)
            if not plan:
                raise ValueError(f"Plan {plan_id} not found")
            
            if not plan.get('paypal_plan_id'):
                raise ValueError(f"Plan {plan_id} missing PayPal plan ID")
            
            # Phase 2: Prepare customer info
            if not customer_info:
                customer_info = self._get_user_info(user_id)
            
            customer_info.update({
                'user_id': user_id,
                'brand_name': 'MarketFit' if app_id == 'marketfit' else 'SalesWit'
            })
            
            # Phase 3: Create subscription with PayPal
            paypal_result = self.paypal.create_subscription(
                plan['paypal_plan_id'],
                customer_info,
                app_id
            )
            
            if paypal_result.get('error'):
                raise ValueError(f"PayPal subscription creation failed: {paypal_result['message']}")
            
            # Phase 4: Store subscription in database (pending approval)
            subscription_data = {
                'id': generate_id('sub_'),
                'user_id': user_id,
                'plan_id': plan_id,
                'paypal_subscription_id': paypal_result['subscription_id'],
                'payment_gateway': 'paypal',
                'status': 'pending_approval',
                'app_id': app_id,
                'gateway_metadata': paypal_result
            }
            
            subscription_id = self._store_subscription(subscription_data)
            
            # Phase 5: Log the creation
            self.db.log_subscription_action(
                subscription_id,
                'paypal_subscription_created',
                {
                    'paypal_subscription_id': paypal_result['subscription_id'],
                    'plan_id': plan_id,
                    'status': 'pending_approval'
                },
                f'user_{user_id}'
            )
            
            return {
                'success': True,
                'subscription_id': subscription_id,
                'paypal_subscription_id': paypal_result['subscription_id'],
                'approval_url': paypal_result['approval_url'],
                'status': 'pending_approval',
                'message': 'PayPal subscription created. User approval required.',
                'requires_approval': True
            }
            
        except Exception as e:
            logger.error(f"Error creating PayPal subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise


    def _store_subscription(self, subscription_data):
        """Store subscription in database"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                INSERT INTO {DB_TABLE_USER_SUBSCRIPTIONS}
                (id, user_id, plan_id, paypal_subscription_id, payment_gateway, 
                status, app_id, gateway_metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """, (
                subscription_data['id'],
                subscription_data['user_id'],
                subscription_data['plan_id'],
                subscription_data['paypal_subscription_id'],
                subscription_data['payment_gateway'],
                subscription_data['status'],
                subscription_data['app_id'],
                json.dumps(subscription_data['gateway_metadata'])
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return subscription_data['id']
            
        except Exception as e:
            logger.error(f"Error storing subscription: {str(e)}")
            raise

    # SUBSCRIPTION UPGRADE FUNCTIONALITY

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

    def _should_block_upgrade(self, current_plan, billing_cycle_info, resource_info):
        """
        Check if upgrade should be blocked based on billing period and resource consumption
        
        Args:
            current_plan: Current subscription plan
            billing_cycle_info: Billing cycle timing information  
            resource_info: Resource utilization data
            
        Returns:
            dict: Block decision with reason
        """
        try:
            # Check if current plan has billing period > 6 months
            interval = current_plan.get('interval', 'month')
            interval_count = current_plan.get('interval_count', 1)
            
            # Calculate total months
            if interval == 'year':
                total_months = interval_count * 12
            elif interval == 'month':
                total_months = interval_count
            else:
                total_months = 1  # Default to monthly
            
            # Only apply blocking logic if billing period > 6 months
            if total_months <= 6:
                return {
                    'should_block': False,
                    'reason': 'billing_period_6_months_or_less'
                }
            
            # Get consumption percentages
            time_consumed_pct = 1 - billing_cycle_info['time_factor']
            resource_consumed_pct = resource_info['base_plan_consumed_pct']
            
            # Check if resource consumption is 20% or more higher than time consumption
            consumption_difference = resource_consumed_pct - time_consumed_pct
            
            if consumption_difference >= 0.20:  # 20% or more difference
                return {
                    'should_block': True,
                    'reason': 'high_resource_consumption',
                    'time_consumed_pct': time_consumed_pct,
                    'resource_consumed_pct': resource_consumed_pct,
                    'consumption_difference': consumption_difference,
                    'billing_period_months': total_months
                }
            
            return {
                'should_block': False,
                'reason': 'consumption_within_limits',
                'time_consumed_pct': time_consumed_pct,
                'resource_consumed_pct': resource_consumed_pct,
                'consumption_difference': consumption_difference
            }
            
        except Exception as e:
            logger.error(f"Error checking upgrade block conditions: {str(e)}")
            # Default to allowing upgrade if check fails
            return {
                'should_block': False,
                'reason': 'error_in_check',
                'error': str(e)
            }

    def upgrade_subscription(self, user_id, subscription_id, new_plan_id, app_id):
        """Upgrade subscription with different logic based on currency and payment method"""
        logger.info(f"[UPGRADE] Service started: user={user_id}, sub={subscription_id}, plan={new_plan_id}")
        
        try:
            # Phase 1: Get current state
            subscription = self._get_subscription_details(subscription_id)
            if not subscription or subscription['user_id'] != user_id:
                raise ValueError("Subscription not found or access denied")

            current_plan = self._get_plan(subscription['plan_id'])
            new_plan = self._get_plan(new_plan_id)

            if not current_plan or not new_plan:
                raise ValueError("Plan not found")

            # Check if it's actually an upgrade
            if new_plan['amount'] <= current_plan['amount']:
                return {
                    'error': True,
                    'error_type': 'downgrade_requested',
                    'message': 'Downgrades involve complex billing adjustments and require manual processing to ensure accuracy. Please contact our support team who will be happy to assist you with your downgrade request.',
                    'action_required': 'contact_support'
                }

            # Phase 2: Get usage and billing data
            usage_data = self.get_current_usage(user_id, subscription_id, app_id)
            if not usage_data:
                raise ValueError("Usage data not found")

            billing_cycle_info = calculate_billing_cycle_info(
                usage_data['billing_period_start'],
                usage_data['billing_period_end']
            )

            resource_info = calculate_resource_utilization(
                usage_data,
                current_plan['features'],
                app_id
            )

            # Phase 3: Route to appropriate upgrade handler based on currency and gateway
            currency, gateway = self._get_currency_and_gateway_from_plan(new_plan)
            
            logger.info(f"[UPGRADE] Currency: {currency}, Gateway: {gateway}")

            if currency == 'INR':
                return self._handle_inr_upgrade_with_payment_method(
                    subscription, current_plan, new_plan, app_id, 
                    billing_cycle_info, resource_info
                )
            else:  # USD
                if gateway == 'razorpay':
                    return self._handle_usd_razorpay_upgrade(
                        subscription, current_plan, new_plan, app_id,
                        billing_cycle_info, resource_info
                    )
                elif gateway == 'paypal':
                    return self._handle_usd_paypal_upgrade(
                        subscription, current_plan, new_plan, app_id,
                        billing_cycle_info, resource_info
                    )
                else:
                    raise ValueError(f"Unsupported gateway for USD: {gateway}")

        except Exception as e:
            logger.error(f"[UPGRADE] Service exception: {str(e)}")
            raise

    def _upgrade_razorpay_subscription(self, subscription, new_plan_id, proration_result):
        """Handle Razorpay subscription upgrade without proration"""
        try:
            logger.info("[UPGRADE] Razorpay upgrade started")
            razorpay_subscription_id = subscription['razorpay_subscription_id']
            
            logger.info("[UPGRADE] Getting new plan details")
            new_plan = self._get_plan(new_plan_id)
            if not new_plan:
                raise ValueError(f"Plan {new_plan_id} not found")
            
            logger.info("[UPGRADE] Calling Razorpay edit API (without proration)")
            # Only change the plan, no proration applied
            response = self.razorpay.client.subscription.edit(razorpay_subscription_id, {
                'plan_id': new_plan_id
            })
            
            logger.info("[UPGRADE] Razorpay API call completed")
            
            if 'error' in response:
                error_msg = response.get('error', {}).get('description', 'Unknown error')
                
                # Handle UPI limitation specifically
                if 'upi' in error_msg.lower():
                    return {
                        'error': True,
                        'error_type': 'upi_upgrade_not_supported',
                        'message': 'Since your subscription is set-up on UPI payments, you need to cancel the subscription and get the new subscription. Or you can contact us using the contact form link at bottom of page.',
                        'support_action': 'cancel_and_resubscribe'
                    }
                
                raise ValueError(f"Razorpay upgrade failed: {error_msg}")
            
            # Phase 2: Update local database
            logger.info("[UPGRADE] Updating local subscription record")
            self._update_subscription_plan(subscription['id'], new_plan_id)
            
            # Phase 3: Update quotas immediately  
            logger.info("[UPGRADE] Updating resource quotas")
            self.initialize_resource_quota(
                subscription['user_id'], 
                subscription['id'], 
                subscription['app_id']
            )
            
            # Phase 4: Log the upgrade
            logger.info("[UPGRADE] Logging upgrade completion")
            self.db.log_subscription_action(
                subscription['id'],
                'razorpay_upgrade_completed',
                {
                    'razorpay_subscription_id': razorpay_subscription_id,
                    'new_plan_id': new_plan_id,
                    'calculated_proration_amount': proration_result.get('prorated_amount', 0),
                    'proration_applied': False,
                    'razorpay_response': response
                },
                f"user_{subscription['user_id']}"
            )
            
            logger.info("[UPGRADE] Razorpay upgrade completed successfully")
            return {
                'success': True,
                'subscription_id': subscription['id'],
                'new_plan_id': new_plan_id,
                'calculated_proration_amount': proration_result.get('prorated_amount', 0),
                'proration_applied': False,
                'razorpay_response': response,
                'message': 'Subscription upgraded successfully (no proration applied)'
            }
            
        except Exception as e:
            error_msg = str(e)
            logger.info(f"[UPGRADE] Razorpay upgrade exception: {error_msg}")
            
            # Handle UPI limitation in exceptions too
            if 'upi' in error_msg.lower():
                return {
                    'error': True,
                    'error_type': 'upi_upgrade_not_supported',
                    'message': 'Since your subscription is set-up on UPI payments, you need to cancel the subscription and get the new subscription. Or you can contact us using the contact form link at bottom of page.',
                    'support_action': 'cancel_and_resubscribe'
                }
            
            raise

    def _handle_inr_upgrade(self, subscription, current_plan, new_plan, app_id, billing_cycle_info, resource_info):
        """Handle INR upgrades - Cancel old and create new with discount"""
        logger.info("[UPGRADE] Handling INR upgrade - cancel and recreate")
        
        try:
            # Calculate value remaining and messaging
            value_remaining_pct = self._calculate_value_remaining_percentage(billing_cycle_info, resource_info)
            value_remaining_amount = value_remaining_pct * self._ensure_float(current_plan['amount'])
            discount_pct_of_new_plan = (value_remaining_amount / self._ensure_float(new_plan['amount'])) * 100
            
            discount_result = self._get_discount_offer_for_value(discount_pct_of_new_plan)
            if discount_result.get('error'):
                return discount_result
            
            discount_offer_pct = discount_result
            discount_amount = (discount_offer_pct / 100) * self._ensure_float(new_plan['amount'])
            
            # Create detailed message
            time_remaining = billing_cycle_info['time_factor'] * 100
            resource_remaining = (1 - resource_info['base_plan_consumed_pct']) * 100
            
            message = (
                f"Upgrading from {current_plan['name']} to {new_plan['name']}. "
                f"Your current plan has {time_remaining:.0f}% time remaining and {resource_remaining:.0f}% resources remaining. "
                f"We're applying a {discount_offer_pct}% discount (₹{discount_amount:.0f} off) to your new plan to account for the remaining value. "
                f"This ensures you only pay for what you use!"
            )
            
            # Phase 1: Cancel current subscription
            logger.info("[UPGRADE] Cancelling current subscription")
            cancel_result = self._cancel_razorpay_subscription_immediately(subscription)
            
            # Phase 2: Create new subscription with discount
            logger.info("[UPGRADE] Creating new subscription with discount")
            new_subscription_result = self._create_subscription_with_discount(
                subscription['user_id'], 
                new_plan['id'], 
                app_id, 
                discount_offer_pct
            )
            
            # Phase 3: Log the upgrade
            self.db.log_subscription_action(
                subscription['id'],
                'inr_upgrade_cancel_recreate',
                {
                    'old_plan_id': current_plan['id'],
                    'new_plan_id': new_plan['id'],
                    'value_remaining_pct': value_remaining_pct,
                    'discount_applied_pct': discount_offer_pct,
                    'cancel_result': cancel_result,
                    'new_subscription': new_subscription_result
                },
                f"user_{subscription['user_id']}"
            )
            
            return {
                'success': True,
                'upgrade_type': 'cancel_and_recreate',
                'old_subscription_id': subscription['id'],
                'new_subscription': new_subscription_result,
                'discount_applied': discount_offer_pct,
                'discount_amount': discount_amount,
                'value_remaining': f"{value_remaining_pct:.1%}",
                'message': message,
                'razorpay_link': new_subscription_result.get('short_url'),
                'calculation_details': {
                    'old_plan_remaining_value': value_remaining_amount,
                    'new_plan_price': new_plan['amount'],
                    'discount_percentage': discount_offer_pct,
                    'you_save': discount_amount
                }
            }
            
        except Exception as e:
            logger.error(f"Error in INR upgrade: {str(e)}")
            raise

    def _handle_usd_razorpay_upgrade(self, subscription, current_plan, new_plan, app_id, billing_cycle_info, resource_info):
        """Handle USD Razorpay upgrades"""
        logger.info("[UPGRADE] Handling USD Razorpay upgrade")
        
        try:
            current_is_monthly = self._is_monthly_plan(current_plan)
            new_is_annual = self._is_annual_plan(new_plan)
            
            if current_is_monthly:
                # a1) Monthly to any higher plan - Use Razorpay's automatic handling
                return self._handle_usd_razorpay_simple_upgrade(subscription, new_plan['id'])
                
            else:  # current is annual
                if not new_is_annual:
                    raise ValueError("Downgrades involve complex billing adjustments and require manual processing to ensure accuracy. Please contact our support team who will be happy to assist you.")
                    
                # a2) Annual to Annual - Complex upgrade with potential additional payment
                return self._handle_usd_razorpay_annual_upgrade(
                    subscription, current_plan, new_plan, app_id, 
                    billing_cycle_info, resource_info
                )
                
        except Exception as e:
            logger.error(f"Error in USD Razorpay upgrade: {str(e)}")
            raise

    def _handle_usd_razorpay_simple_upgrade(self, subscription, new_plan_id):
        """Handle simple USD Razorpay upgrade"""
        try:
            razorpay_subscription_id = subscription['razorpay_subscription_id']
            
            # Razorpay USD allows plan changes
            response = self.razorpay.client.subscription.edit(razorpay_subscription_id, {
                'plan_id': new_plan_id
            })
            
            if 'error' in response:
                raise ValueError(f"Razorpay upgrade failed: {response.get('error', {}).get('description')}")
            
            # Update local database and initialize full quota immediately
            self._update_subscription_plan(subscription['id'], new_plan_id)
            self.initialize_resource_quota(subscription['user_id'], subscription['id'], subscription['app_id'])
            
            return {
                'success': True,
                'upgrade_type': 'razorpay_plan_change',
                'subscription_id': subscription['id'],
                'new_plan_id': new_plan_id,
                'message': 'Subscription upgraded successfully with Razorpay automatic billing.'
            }
            
        except Exception as e:
            logger.error(f"Error in simple USD Razorpay upgrade: {str(e)}")
            raise

    def _handle_usd_razorpay_annual_upgrade(self, subscription, current_plan, new_plan, app_id, billing_cycle_info, resource_info):
        """Handle annual to annual USD Razorpay upgrade with potential additional payment"""
        try:
            # First do the standard upgrade
            simple_result = self._handle_usd_razorpay_simple_upgrade(subscription, new_plan['id'])
            
            # Add temporary resources first
            self._add_temporary_resources(subscription['user_id'], subscription['id'], app_id)
            
            # Check if additional payment is needed
            time_remaining_pct = billing_cycle_info['time_factor']
            resource_remaining_pct = 1 - resource_info['base_plan_consumed_pct']
            
            # Check if resources left% < time left% by 5%
            if (time_remaining_pct - resource_remaining_pct) >= 0.05:
                # Calculate additional payment
                excess_consumption_pct = (time_remaining_pct - resource_remaining_pct) - 0.05
                additional_amount = excess_consumption_pct * self._ensure_float(current_plan['amount'])
                
                # Create additional invoice
                additional_payment_result = self._create_additional_payment_invoice(
                    subscription, additional_amount, 'USD'
                )
                 # Enhanced message with calculation
                message = (
                    f'Subscription upgraded with temporary resources. Additional payment of ${additional_amount:.2f} required. '
                    f'Calculation: You have {time_remaining_pct:.1%} time remaining but only {resource_remaining_pct:.1%} resources left. '
                    f'The excess consumption of {excess_consumption_pct:.1%} × ${current_plan["amount"]} = ${additional_amount:.2f}.'
                )
                simple_result.update({
                    'additional_payment_required': True,
                    'additional_amount': additional_amount,
                    'additional_payment_link': additional_payment_result.get('short_url'),
                    'message': message,
                    'temporary_resources_added': True
                })
            else:
                simple_result.update({
                    'temporary_resources_added': True,
                    'message': 'Subscription upgraded successfully. Temporary resources added.'
                })
            
            return simple_result
            
        except Exception as e:
            logger.error(f"Error in annual USD Razorpay upgrade: {str(e)}")
            raise

    def _handle_usd_paypal_upgrade(self, subscription, current_plan, new_plan, app_id, billing_cycle_info, resource_info):
        """Handle USD PayPal upgrades"""
        logger.info("[UPGRADE] Handling USD PayPal upgrade")
        
        try:
            current_is_monthly = self._is_monthly_plan(current_plan)
            new_is_annual = self._is_annual_plan(new_plan)
            
            if current_is_monthly:
                # b1) Monthly to Monthly/Annual - Immediate update with temp resources
                return self._handle_usd_paypal_simple_upgrade(subscription, new_plan, app_id)
                
            else:  # current is annual
                if not new_is_annual:
                    raise ValueError("Cannot downgrade from annual to monthly plan")
                    
                # b2) Annual to Annual - Proration payment then update
                return self._handle_usd_paypal_annual_upgrade(
                    subscription, current_plan, new_plan, app_id, 
                    billing_cycle_info, resource_info
                )
                
        except Exception as e:
            logger.error(f"Error in USD PayPal upgrade: {str(e)}")
            raise

    def _handle_usd_paypal_simple_upgrade(self, subscription, new_plan, app_id):
        """Handle simple USD PayPal upgrade"""
        try:
            paypal_subscription_id = subscription['paypal_subscription_id']
            new_paypal_plan_id = new_plan['paypal_plan_id']
            
            # Update PayPal subscription immediately
            result = self.paypal.update_subscription_plan_only(
                paypal_subscription_id,
                new_paypal_plan_id
            )
            
            if result.get('error'):
                raise ValueError(f"PayPal upgrade failed: {result['message']}")
            
            # Update local database
            self._update_subscription_plan(subscription['id'], new_plan['id'])
            
            # Add temporary resources (double free plan)
            self._add_temporary_resources(subscription['user_id'], subscription['id'], app_id)
            
            return {
                'success': True,
                'upgrade_type': 'paypal_immediate',
                'subscription_id': subscription['id'],
                'new_plan_id': new_plan['id'],
                'message': 'PayPal subscription upgraded successfully. Temporary resources added until next billing cycle.',
                'temporary_resources_added': True,
                'requires_approval': result.get('requires_approval', False),
                'approval_url': result.get('approval_url')
            }
            
        except Exception as e:
            logger.error(f"Error in simple USD PayPal upgrade: {str(e)}")
            raise

    def _handle_usd_paypal_annual_upgrade(self, subscription, current_plan, new_plan, app_id, billing_cycle_info, resource_info):
        """Handle annual to annual USD PayPal upgrade with proration payment"""
        try:
            # Calculate proration amount and messaging
            value_remaining_pct = self._calculate_value_remaining_percentage(billing_cycle_info, resource_info)
            remaining_period_value = value_remaining_pct * self._ensure_float(new_plan['amount'])
            
            time_remaining = billing_cycle_info['time_factor'] * 100
            resource_remaining = (1 - resource_info['base_plan_consumed_pct']) * 100
            
            message = (
                f"Upgrading from {current_plan['name']} to {new_plan['name']}. "
                f"You have {time_remaining:.0f}% time remaining and {resource_remaining:.0f}% resources remaining in your current billing cycle. "
                f"We're charging you ${remaining_period_value:.2f} for the upgraded features for the remaining period. "
                f"This is calculated based on the minimum of time/resources remaining to ensure fair billing."
            )
            
            # Create one-time PayPal payment for proration
            proration_payment_result = self._create_paypal_one_time_payment(
                remaining_period_value,
                subscription,
                'Upgrade proration payment'
            )
            
            if proration_payment_result.get('error'):
                raise ValueError(f"Failed to create proration payment: {proration_payment_result['message']}")
            
            # Store pending upgrade details
            self._store_pending_paypal_upgrade(
                subscription['id'],
                new_plan['id'],
                proration_payment_result['order_id']
            )
            
            return {
                'success': True,
                'upgrade_type': 'paypal_with_proration',
                'subscription_id': subscription['id'],
                'new_plan_id': new_plan['id'],
                'proration_amount': remaining_period_value,
                'payment_required': True,
                'payment_url': proration_payment_result['approval_url'],
                'message': message,
                'order_id': proration_payment_result['order_id'],
                'calculation_details': {
                    'time_remaining': f"{time_remaining:.0f}%",
                    'resources_remaining': f"{resource_remaining:.0f}%",
                    'billing_basis': 'minimum of time and resources remaining',
                    'proration_charge': remaining_period_value
                }
            }
            
        except Exception as e:
            logger.error(f"Error in annual USD PayPal upgrade: {str(e)}")
            raise

        # Supporting methods
    def _cancel_razorpay_subscription_immediately(self, subscription):
        """Cancel Razorpay subscription immediately"""
        try:
            if isinstance(subscription, dict) and subscription.get('razorpay_subscription_id'):
                razorpay_subscription_id = subscription.get('razorpay_subscription_id')
            else:
                razorpay_subscription_id = subscription  # Assume it's a direct ID
                
            if not razorpay_subscription_id:
                return {'success': True, 'message': 'No Razorpay subscription to cancel'}
                
            result = self.razorpay.cancel_subscription(
                razorpay_subscription_id,
                cancel_at_cycle_end=False
            )
            
            if not result.get('error'):
                self._update_subscription_status_by_razorpay_id(
                    razorpay_subscription_id, 
                    'cancelled'
                )
            
            return result
        except Exception as e:
            logger.error(f"Error cancelling Razorpay subscription: {str(e)}")
            return {'error': True, 'message': str(e)}
        
    def _create_subscription_with_discount(self, user_id, plan_id, app_id, discount_pct):
        """Create new subscription with discount offer"""
        try:
            user = self._get_user_info(user_id)
            if not user:
                raise ValueError("User not found")
            
            plan = self._get_plan(plan_id)
            if not plan:
                raise ValueError("Plan not found")
            
            customer_info = {
                'user_id': user['google_uid'], 
                'email': user.get('email'), 
                'name': user.get('display_name')
            }
            
            additional_notes = {
                'discount_offer': discount_pct,
                'upgrade_discount': True
            }
            
            response = self.razorpay.create_subscription_with_offer(
                plan['razorpay_plan_id'] or plan['id'],
                customer_info,
                app_id,
                discount_offer_pct=discount_pct,
                additional_notes=additional_notes
            )
            
            if response.get('error'):
                raise ValueError(response.get('message', 'Failed to create subscription'))
            
            return self._save_paid_subscription(user_id, plan_id, app_id, response)
            
        except Exception as e:
            logger.error(f"Error creating subscription with discount: {str(e)}")
            raise

    def _update_subscription_status_by_razorpay_id(self, razorpay_subscription_id, status):
        """Update subscription status by Razorpay ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = %s, updated_at = NOW()
                WHERE razorpay_subscription_id = %s
            """, (status, razorpay_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating subscription status: {str(e)}")

    def _add_temporary_resources(self, user_id, subscription_id, app_id):
        """Add double free plan resources temporarily"""
        try:
            free_plan = self._get_free_plan(app_id)
            if not free_plan:
                logger.warning(f"No free plan found for {app_id}")
                return
            
            free_features = parse_json_field(free_plan.get('features', '{}'))
            
            if app_id == 'marketfit':
                temp_doc_pages = free_features.get('document_pages', 50) * 2
                temp_perplexity = free_features.get('perplexity_requests', 20) * 2
                temp_requests = 0
            else:  # saleswit
                temp_doc_pages = 0
                temp_perplexity = 0
                temp_requests = free_features.get('requests', 20) * 2
            
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

    def _create_additional_payment_invoice(self, subscription, amount, currency):
        """Create additional payment invoice for excess consumption"""
        try:
            invoice_data = {
                'amount': int(amount * 100),
                'currency': currency,
                'description': 'Additional payment for excess resource consumption',
                'customer': {
                    'email': subscription.get('user_email'),
                    'name': subscription.get('user_name')
                },
                'notes': {
                    'subscription_id': subscription['id'],
                    'payment_type': 'excess_consumption'
                }
            }
            
            result = self.razorpay.create_payment_link(invoice_data)
            return result
            
        except Exception as e:
            logger.error(f"Error creating additional payment invoice: {str(e)}")
            return {'error': True, 'message': str(e)}

    def _create_paypal_one_time_payment(self, amount, subscription, description):
        """Create one-time PayPal payment for proration"""
        try:
            payment_data = {
                'amount': amount,
                'currency': 'USD',
                'description': description,
                'customer_info': {
                    'user_id': subscription['user_id']
                },
                'metadata': {
                    'subscription_id': subscription['id'],
                    'payment_type': 'upgrade_proration'
                }
            }
            
            result = self.paypal.create_one_time_payment(payment_data)
            return result
            
        except Exception as e:
            logger.error(f"Error creating PayPal one-time payment: {str(e)}")
            return {'error': True, 'message': str(e)}

    def _store_pending_paypal_upgrade(self, subscription_id, new_plan_id, order_id):
        """Store pending upgrade details"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (json.dumps({
                'pending_upgrade': {
                    'new_plan_id': new_plan_id,
                    'order_id': order_id,
                    'created_at': datetime.now().isoformat()
                }
            }), subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error storing pending upgrade: {str(e)}")

    def handle_additional_payment_completion(self, payment_id, subscription_id):
        """Handle completion of additional payment for USD annual upgrade"""
        try:
            payment_status = self.razorpay.client.payment.fetch(payment_id)
            
            if payment_status.get('status') != 'captured':
                return {'error': True, 'message': 'Payment not completed'}
            
            subscription = self._get_subscription_details(subscription_id)
            if not subscription:
                return {'error': True, 'message': 'Subscription not found'}
            
            # Replace temporary resources with full quota for new plan
            self.initialize_resource_quota(
                subscription['user_id'], 
                subscription_id, 
                subscription['app_id']
            )
            
            self.db.log_subscription_action(
                subscription_id,
                'additional_payment_completed',
                {
                    'payment_id': payment_id,
                    'amount': payment_status.get('amount'),
                    'status': payment_status.get('status')
                },
                f"user_{subscription['user_id']}"
            )
            
            return {
                'success': True,
                'message': 'Additional payment processed, full resources activated'
            }
            
        except Exception as e:
            logger.error(f"Error handling additional payment completion: {str(e)}")
            return {'error': True, 'message': str(e)}

    def handle_paypal_proration_completion(self, order_id):
        """Handle completion of PayPal proration payment"""
        try:
            capture_result = self.paypal.capture_order_payment(order_id)
            
            if capture_result.get('error'):
                return {'error': True, 'message': 'Failed to capture payment'}
            
            subscription = self._find_subscription_by_proration_payment(order_id)
            if not subscription:
                return {'error': True, 'message': 'Subscription not found'}
            
            pending_upgrade = subscription.get('metadata', {}).get('pending_upgrade', {})
            new_plan_id = pending_upgrade.get('new_plan_id')
            
            if not new_plan_id:
                return {'error': True, 'message': 'No pending upgrade found'}
            
            # Update PayPal subscription
            paypal_result = self.paypal.update_subscription_plan_only(
                subscription['paypal_subscription_id'],
                self._get_plan(new_plan_id)['paypal_plan_id']
            )
            
            # Update local database and set full quota
            self._update_subscription_plan(subscription['id'], new_plan_id)
            self.initialize_resource_quota(subscription['user_id'], subscription['id'], subscription['app_id'])
            
            # Clear pending upgrade
            self._clear_pending_upgrade(subscription['id'])
            
            return {
                'success': True,
                'message': 'Proration payment completed, subscription upgraded'
            }
            
        except Exception as e:
            logger.error(f"Error handling PayPal proration completion: {str(e)}")
            return {'error': True, 'message': str(e)}

    def _find_subscription_by_proration_payment(self, order_id):
        """Find subscription with pending upgrade matching payment ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE JSON_EXTRACT(metadata, '$.pending_upgrade.order_id') = %s
            """, (order_id,))
            
            subscription = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return subscription
            
        except Exception as e:
            logger.error(f"Error finding subscription by proration payment: {str(e)}")
            return None

    def _clear_pending_upgrade(self, subscription_id):
        """Clear pending upgrade metadata"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET metadata = JSON_REMOVE(IFNULL(metadata, '{{}}'), '$.pending_upgrade'),
                    updated_at = NOW()
                WHERE id = %s
            """, (subscription_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error clearing pending upgrade: {str(e)}")

    def _upgrade_paypal_subscription(self, subscription, new_plan_id, proration_result):
        """Handle PayPal subscription upgrade without proration"""
        try:
            paypal_subscription_id = subscription['paypal_subscription_id']
            
            # Get the new plan's PayPal plan ID
            new_plan = self._get_plan(new_plan_id)
            if not new_plan or not new_plan.get('paypal_plan_id'):
                raise ValueError(f"New plan {new_plan_id} missing PayPal plan ID")
            
            new_paypal_plan_id = new_plan['paypal_plan_id']
            
            # Phase 1: Update PayPal subscription WITHOUT proration
            paypal_result = self.paypal.update_subscription(
                paypal_subscription_id,
                new_paypal_plan_id,
                proration_amount=0  # No proration applied
            )
            
            if paypal_result.get('error'):
                raise ValueError(f"PayPal upgrade failed: {paypal_result['message']}")
            
            # Phase 2: Update local database
            self._update_subscription_plan(subscription['id'], new_plan_id)
            
            # Phase 3: Update quotas immediately
            self.initialize_resource_quota(
                subscription['user_id'], 
                subscription['id'], 
                subscription['app_id']
            )
            
            # Phase 4: Log the upgrade
            self.db.log_subscription_action(
                subscription['id'],
                'paypal_upgrade_completed',
                {
                    'paypal_subscription_id': paypal_subscription_id,
                    'new_plan_id': new_plan_id,
                    'new_paypal_plan_id': new_paypal_plan_id,
                    'calculated_proration_amount': proration_result.get('prorated_amount', 0),
                    'proration_applied': False,
                    'paypal_response': paypal_result
                },
                f"user_{subscription['user_id']}"
            )
            
            return {
                'success': True,
                'subscription_id': subscription['id'],
                'new_plan_id': new_plan_id,
                'calculated_proration_amount': proration_result.get('prorated_amount', 0),
                'proration_applied': False,
                'paypal_result': paypal_result,
                'approval_url': paypal_result.get('approval_url'),
                'message': 'PayPal subscription upgraded successfully (no proration applied)',
                'note': 'If approval URL is present, user may need to approve the change'
            }
            
        except Exception as e:
            logger.error(f"Error upgrading PayPal subscription: {str(e)}")
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

    def get_subscription_by_gateway_id(self, gateway_subscription_id, provider):
        """Get subscription by gateway ID (used by webhooks and upgrades)"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            if provider == 'razorpay':
                id_column = 'razorpay_subscription_id'
            elif provider == 'paypal':
                id_column = 'paypal_subscription_id'
            else:
                raise ValueError(f"Unknown provider: {provider}")
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE {id_column} = %s
            """, (gateway_subscription_id,))
            
            subscription = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting subscription by gateway ID: {str(e)}")
            return None

    # ADDON PURCHASE FUNCTIONALITY

    def purchase_addon(self, user_id, app_id, addon_type, quantity, amount_paid, payment_id=None):
        """
        Purchase additional resources as addon
        
        Args:
            user_id: User's google_uid
            app_id: 'marketfit' or 'saleswit'
            addon_type: 'document_pages', 'perplexity_requests', or 'requests'
            quantity: Number of resources to add
            amount_paid: Amount paid for addon
            payment_id: Payment gateway transaction ID
            
        Returns:
            dict: Purchase result
        """
        logger.info(f"Processing addon purchase for user {user_id}: {quantity} {addon_type}")
        
        try:
            # Phase 1: Get user's current subscription and billing period
            subscription = self.get_user_subscription(user_id, app_id)
            if not subscription:
                raise ValueError("No active subscription found")
            
            # Phase 2: Validate addon type for app
            self._validate_addon_type(app_id, addon_type)
            
            # Phase 3: Record addon purchase
            addon_id = self._record_addon_purchase(
                user_id, subscription['id'], app_id, addon_type, 
                quantity, amount_paid, payment_id, subscription
            )
            
            # Phase 4: Update main quota columns immediately
            self._add_addon_to_quota(user_id, subscription['id'], app_id, addon_type, quantity)
            
            # Phase 5: Log the purchase
            self.db.log_subscription_action(
                subscription['id'],
                'addon_purchased',
                {
                    'addon_type': addon_type,
                    'quantity': quantity,
                    'amount_paid': amount_paid,
                    'addon_id': addon_id
                },
                f'user_{user_id}'
            )
            
            return {
                'success': True,
                'addon_id': addon_id,
                'addon_type': addon_type,
                'quantity': quantity,
                'expires_at': subscription['current_period_end'],
                'message': f'Successfully added {quantity} {addon_type} to your account'
            }
            
        except Exception as e:
            logger.error(f"Error purchasing addon: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def _validate_addon_type(self, app_id, addon_type):
        """Validate addon type is valid for the app"""
        valid_addons = {
            'marketfit': ['document_pages', 'perplexity_requests'],
            'saleswit': ['requests']
        }
        
        if addon_type not in valid_addons.get(app_id, []):
            raise ValueError(f"Invalid addon type '{addon_type}' for app '{app_id}'")

    def _record_addon_purchase(self, user_id, subscription_id, app_id, addon_type, quantity, amount_paid, payment_id, subscription):
        """Record addon purchase in database"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            addon_id = generate_id('addon_')
            
            cursor.execute("""
                INSERT INTO resource_addons 
                (id, user_id, subscription_id, app_id, addon_type, quantity, 
                amount_paid, billing_period_start, billing_period_end, payment_id, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            """, (
                addon_id, user_id, subscription_id, app_id, addon_type, quantity,
                amount_paid, subscription['current_period_start'], 
                subscription['current_period_end'], payment_id
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return addon_id
            
        except Exception as e:
            logger.error(f"Error recording addon purchase: {str(e)}")
            raise

    def _add_addon_to_quota(self, user_id, subscription_id, app_id, addon_type, quantity):
        """Add addon quantity to main quota columns"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Map addon_type to quota column
            quota_column = f"{addon_type}_quota"
            addon_tracking_column = f"current_addon_{addon_type}"
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_RESOURCE_USAGE}
                SET {quota_column} = {quota_column} + %s,
                    {addon_tracking_column} = {addon_tracking_column} + %s,
                    updated_at = NOW()
                WHERE user_id = %s AND subscription_id = %s AND app_id = %s
            """, (quantity, quantity, user_id, subscription_id, app_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Added {quantity} {addon_type} to user {user_id} quota")
            
        except Exception as e:
            logger.error(f"Error adding addon to quota: {str(e)}")
            raise

    # UPDATED QUOTA RESET LOGIC
    def reset_quota_on_renewal(self, subscription_id):
        """
        Reset resource quota when subscription renews
        Addons expire and are not carried over
        """
        try:
            subscription_details = self._get_subscription_with_features(subscription_id)
            
            if not subscription_details:
                logger.error(f"Subscription {subscription_id} not found")
                return False
            
            # Parse features and calculate BASE plan quota values only
            features = self._parse_subscription_features(subscription_details.get('features', '{}'))
            base_quota_values = self._calculate_quota_values(subscription_details.get('app_id'), features)
            
            # Reset to base plan only - expire all addons
            return self._reset_quota_to_base_plan(subscription_details, base_quota_values)
            
        except Exception as e:
            logger.error(f"Error resetting quota on renewal: {str(e)}")
            return False

    def _reset_quota_to_base_plan(self, subscription_details, base_quota_values):
        """Reset quota to base plan and expire addons"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            
            
            try:
                # Reset quota to base plan values only
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
                        billing_period_start = %s,
                        billing_period_end = %s,
                        updated_at = NOW()
                    WHERE user_id = %s AND subscription_id = %s AND app_id = %s
                """, (
                    base_quota_values['document_pages_quota'],
                    base_quota_values['perplexity_requests_quota'],
                    base_quota_values['requests_quota'],
                    base_quota_values['document_pages_quota'],  # Original = base plan
                    base_quota_values['perplexity_requests_quota'],
                    base_quota_values['requests_quota'],
                    subscription_details['current_period_start'],
                    subscription_details['current_period_end'],
                    subscription_details['user_id'],
                    subscription_details['id'],
                    subscription_details['app_id']
                ))
                
                # Mark all addons from previous billing cycle as expired
                cursor.execute("""
                    UPDATE resource_addons
                    SET status = 'expired'
                    WHERE subscription_id = %s 
                    AND billing_period_end <= %s
                    AND status = 'active'
                """, (subscription_details['id'], subscription_details['current_period_start']))
                
                conn.commit()
                
                logger.info(f"Reset quota to base plan for subscription {subscription_details['id']}")
                return True
                
            except Exception as e:
                conn.rollback()
                raise
                
        except Exception as e:
            logger.error(f"Error resetting quota to base plan: {str(e)}")
            raise
        finally:
            cursor.close()
            conn.close()

    # UPDATED QUOTA INITIALIZATION WITH ORIGINAL TRACKING
    def initialize_resource_quota(self, user_id, subscription_id, app_id):
        """
        Initialize or reset resource quota for a subscription period
        Enhanced to track original quotas for proration
        """
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

    # UPDATED CANCELLATION LOGIC FOR PAYPAL
    def cancel_subscription(self, user_id, subscription_id):
        """
        Cancel a user's subscription
        Razorpay: at the end of the billing cycle
        PayPal: immediately but keep access until period end
        """
        try:
            # Phase 1: Get subscription data
            subscription = self._get_subscription_for_cancellation(user_id, subscription_id)
            
            # Phase 2: Handle based on gateway
            if subscription.get('razorpay_subscription_id'):
                result = self._cancel_razorpay_subscription(subscription)
            elif subscription.get('paypal_subscription_id'):
                result = self._cancel_paypal_subscription(subscription)
            else:
                raise ValueError("No gateway subscription found")
            
            # Phase 3: Log cancellation
            self.db.log_subscription_action(
                subscription_id,
                'cancellation_requested',
                {
                    'gateway': 'razorpay' if subscription.get('razorpay_subscription_id') else 'paypal',
                    'cancelled_by': f'user_{user_id}',
                    'cancellation_details': result
                },
                f'user_{user_id}'
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error cancelling subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def _cancel_razorpay_subscription(self, subscription):
        """Cancel Razorpay subscription at end of cycle"""
        try:
            # Use the existing Razorpay provider to cancel
            result = self.razorpay.cancel_subscription(
                subscription['razorpay_subscription_id'],
                cancel_at_cycle_end=True
            )
            
            if result.get('error'):
                logger.error(f"Error scheduling cancellation with Razorpay: {result.get('message')}")
            else:
                logger.info(f"Razorpay subscription scheduled for cancellation: {subscription['razorpay_subscription_id']}")
            
            # Mark in database (keep status as active until actually cancelled)
            return self._mark_subscription_scheduled_for_cancellation(subscription['id'], subscription)
            
        except Exception as e:
            logger.error(f"Error cancelling Razorpay subscription: {str(e)}")
            raise

    def _cancel_paypal_subscription(self, subscription):
        """Cancel PayPal subscription immediately but keep access"""
        try:
            paypal_subscription_id = subscription['paypal_subscription_id']
            
            # Cancel with PayPal using our provider
            result = self.paypal.cancel_subscription(paypal_subscription_id)
            
            if result.get('error'):
                logger.error(f"PayPal cancellation failed: {result['message']}")
                raise ValueError(f"PayPal cancellation failed: {result['message']}")
            
            logger.info(f"PayPal subscription cancelled: {paypal_subscription_id}")
            
            # Mark as cancelled in database but keep active until period end
            return self._mark_paypal_subscription_cancelled(subscription['id'], subscription)
            
        except Exception as e:
            logger.error(f"Error cancelling PayPal subscription: {str(e)}")
            raise

    def _mark_subscription_scheduled_for_cancellation(self, subscription_id, subscription):
        """Mark Razorpay subscription as scheduled for cancellation"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            current_time_str = datetime.now().isoformat()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s), 
                    updated_at = NOW()
                WHERE id = %s
            """, (json.dumps({
                'cancellation_scheduled': True,
                'cancelled_at': current_time_str,
                'cancellation_type': 'end_of_cycle'
            }), subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "id": subscription_id,
                "status": "active",  # Status remains active
                "cancellation_scheduled": True,
                "cancellation_type": "end_of_cycle",
                "end_date": subscription.get('current_period_end').isoformat() if subscription.get('current_period_end') else None,
                "message": "Subscription will remain active until the end of the current billing period"
            }
            
        except Exception as e:
            logger.error(f"Error marking subscription scheduled for cancellation: {str(e)}")
            raise

    def _execute_cancel_and_recreate_with_discount(self, user_id, subscription_id, current_plan, new_plan, 
                                                app_id, discount_pct, discount_amount, payment_method, value_remaining_pct):
        """Execute cancel and recreate flow with discount offer"""
        try:
            # Step 1: Cancel current subscription
            cancel_result = self._cancel_razorpay_subscription_immediately(
                self._get_subscription_by_id(subscription_id)
            )
            
            if cancel_result.get('error'):
                logger.error(f"Error cancelling subscription during upgrade: {cancel_result.get('message')}")
                
            # Step 2: Get appropriate offer ID for the discount percentage and payment method
            offer_id = self._get_razorpay_offer_id(discount_pct, payment_method)
            
            # Step 3: Create new subscription with discount
            new_subscription = self._create_subscription_with_specific_offer(
                user_id, new_plan['id'], app_id, offer_id, payment_method
            )
            
            # Step 4: Log the upgrade action
            self.db.log_subscription_action(
                subscription_id,
                f"upgrade_{payment_method}_with_discount",
                {
                    'old_plan_id': current_plan['id'],
                    'new_plan_id': new_plan['id'],
                    'discount_pct': discount_pct,
                    'discount_amount': discount_amount,
                    'offer_id': offer_id,
                    'value_remaining_pct': value_remaining_pct
                },
                f"user_{user_id}"
            )
            
            # Step 5: Return success with discount details
            return {
                'success': True,
                'upgrade_type': 'cancel_and_recreate_with_discount',
                'payment_method': payment_method,
                'old_subscription_id': subscription_id, 
                'new_subscription': new_subscription,
                'discount_applied': discount_pct,
                'discount_amount': discount_amount,
                'final_amount': float(new_plan['amount']) - float(discount_amount),
                'offer_id_used': offer_id,
                'razorpay_link': new_subscription.get('short_url'),
                'currency': current_plan.get('currency', 'INR')
            }
            
        except Exception as e:
            logger.error(f"Error in cancel and recreate with discount: {str(e)}")
            return {
                'success': False, 
                'error': str(e),
                'error_type': 'execution_error'
            }

    def _execute_cancel_and_recreate_with_refund(self, user_id, subscription_id, current_plan, new_plan, 
                                            app_id, refund_amount, payment_method):
        """Execute cancel and recreate flow with manual refund"""
        try:
            # Step 1: Cancel current subscription
            cancel_result = self._cancel_razorpay_subscription_immediately(
                self._get_subscription_by_id(subscription_id)
            )
            
            # Step 2: Create new subscription at full price
            new_subscription = self._create_subscription_full_price(
                user_id, new_plan['id'], app_id
            )
            
            # Step 3: Schedule manual refund
            refund_id = self._schedule_manual_refund(
                user_id, subscription_id, refund_amount, current_plan, payment_method
            )
            
            # Step 4: Log the upgrade action
            self.db.log_subscription_action(
                subscription_id,
                f"upgrade_{payment_method}_with_refund",
                {
                    'old_plan_id': current_plan['id'],
                    'new_plan_id': new_plan['id'],
                    'refund_amount': refund_amount,
                    'refund_id': refund_id
                },
                f"user_{user_id}"
            )
            
            # Step 5: Return success with refund details
            return {
                'success': True,
                'upgrade_type': 'cancel_and_recreate_with_refund',
                'payment_method': payment_method,
                'old_subscription_id': subscription_id,
                'new_subscription': new_subscription,
                'refund_amount': refund_amount,
                'refund_id': refund_id,
                'razorpay_link': new_subscription.get('short_url'),
                'currency': current_plan.get('currency', 'INR')
            }
            
        except Exception as e:
            logger.error(f"Error in cancel and recreate with refund: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'error_type': 'execution_error'
            }

    def _get_subscription_by_id(self, subscription_id):
        """Get subscription object by ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE id = %s
            """, (subscription_id,))
            
            subscription = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return subscription
        except Exception as e:
            logger.error(f"Error getting subscription by ID: {str(e)}")
            return None

    def _mark_paypal_subscription_cancelled(self, subscription_id, subscription):
        """Mark PayPal subscription as cancelled but keep access until period end"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            current_time_str = datetime.now().isoformat()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s), 
                    updated_at = NOW()
                WHERE id = %s
            """, (json.dumps({
                'paypal_cancelled': True,
                'cancelled_at': current_time_str,
                'cancellation_type': 'immediate_with_access'
            }), subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "id": subscription_id,
                "status": "active",  # Keep active until period end
                "paypal_cancelled": True,
                "cancellation_type": "immediate_with_access",
                "end_date": subscription.get('current_period_end').isoformat() if subscription.get('current_period_end') else None,
                "message": "PayPal subscription cancelled. Access continues until end of billing period."
            }
            
        except Exception as e:
            logger.error(f"Error marking PayPal subscription cancelled: {str(e)}")
            raise

payment_service = PaymentService()