"""
Main payment service class for payment gateway operations
"""
import json
import logging
import traceback
from datetime import datetime, timedelta

from .db import DatabaseManager
from .providers.razorpay_provider import RazorpayProvider
from .providers.paypal_provider import PayPalProvider
from .utils.helpers import generate_id, calculate_period_end, parse_json_field
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
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the plan details
            cursor.execute(f"SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS} WHERE id = %s", (plan_id,))
            plan = cursor.fetchone()
            
            if not plan:
                cursor.close()
                conn.close()
                raise ValueError(f"Plan with ID {plan_id} not found")
            
            # Check if user already has an active subscription
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS} 
                WHERE user_id = %s AND app_id = %s AND status = 'active'
            """, (user_id, app_id))
            
            existing_subscription = cursor.fetchone()
            
            # If free plan, just create a database entry
            if plan['amount'] == 0:
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
                cursor.close()
                conn.close()
                
                return {
                    'id': subscription_id,
                    'user_id': user_id,
                    'plan_id': plan_id,
                    'status': 'active',
                    'app_id': app_id
                }
            
            # For paid plans, get customer info
            cursor.execute("SELECT email, display_name FROM users WHERE id = %s OR google_uid = %s", (user_id, user_id))
            user = cursor.fetchone()
            
            if not user:
                cursor.close()
                conn.close()
                raise ValueError(f"User with ID {user_id} not found")
            
            # Get payment gateways from plan
            payment_gateways = parse_json_field(plan.get('payment_gateways'), ['razorpay'])
            
            # Determine which gateway to use (use first in list)
            gateway = payment_gateways[0] if payment_gateways else 'razorpay'
            
            # Create gateway-specific subscription
            gateway_sub_id = None
            gateway_response = None
            
            if gateway == 'razorpay':
                # Use the Razorpay provider
                gateway_plan_id = plan.get('razorpay_plan_id') or plan_id
                
                response = self.razorpay.create_subscription(
                    gateway_plan_id,
                    {'user_id': user_id, 'email': user.get('email'), 'name': user.get('display_name')},
                    app_id
                )
                
                if response.get('error'):
                    raise ValueError(response.get('message', 'Failed to create Razorpay subscription'))
                
                gateway_sub_id = response.get('id')
                gateway_response = response
                
            elif gateway == 'paypal':
                # Use the PayPal provider
                gateway_plan_id = plan.get('paypal_plan_id')
                
                if not gateway_plan_id:
                    raise ValueError("PayPal plan ID not found for this plan")
                
                response = self.paypal.create_subscription(
                    gateway_plan_id,
                    {'user_id': user_id, 'email': user.get('email'), 'name': user.get('display_name')},
                    app_id
                )
                
                if response.get('error'):
                    raise ValueError(response.get('message', 'Failed to create PayPal subscription'))
                
                gateway_sub_id = response.get('id')
                gateway_response = response
            
            else:
                raise ValueError(f"Unsupported payment gateway: {gateway}")
            
            # Save the subscription details to the database
            subscription_id = generate_id('sub_')
            
            # Set the appropriate field based on gateway
            razorpay_subscription_id = gateway_sub_id if gateway == 'razorpay' else None
            paypal_subscription_id = gateway_sub_id if gateway == 'paypal' else None
            
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
            cursor.close()
            conn.close()
                
            # Return the subscription with the checkout URL if available
            return {
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
            
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
    def create_paypal_subscription(self, user_id, plan_id, paypal_subscription_id, app_id='marketfit'):
        """
        Create a subscription record for a PayPal subscription
        
        Args:
            user_id: The user's ID
            plan_id: The plan ID
            paypal_subscription_id: The PayPal subscription ID
            app_id: The application ID
            
        Returns:
            dict: Subscription details
        """
        logger.info(f"Creating PayPal subscription for user {user_id}, plan {plan_id}")
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the plan details
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS}
                WHERE id = %s AND app_id = %s
            """, (plan_id, app_id))
            
            plan = cursor.fetchone()
            
            if not plan:
                logger.error(f"Plan not found: {plan_id}")
                cursor.close()
                conn.close()
                return {'error': 'Plan not found'}
            
            # Check if the user already has an active subscription
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE user_id = %s AND app_id = %s AND status = 'active'
            """, (user_id, app_id))
            
            existing_subscription = cursor.fetchone()
            
            # Calculate subscription period
            start_date = datetime.now()
            period_end = calculate_period_end(start_date, plan['interval'], plan['interval_count'])
            
            if existing_subscription:
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
                """, (plan_id, paypal_subscription_id, start_date, period_end, existing_subscription['id']))
                
                subscription_id = existing_subscription['id']
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
        
            
            # Commit the transaction
            conn.commit()
            
            # Get the updated subscription record
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
            logger.error(f"Error creating PayPal subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return {'error': str(e)}
    
    def get_user_subscription(self, user_id, app_id):
        """
        Get a user's active subscription for a specific app
        
        Args:
            user_id: The user's ID
            app_id: The application ID
            
        Returns:
            dict: Subscription details
        """
        logger.info(f"Getting subscription for user {user_id}, app {app_id}")
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get user's active subscription
            cursor.execute(f"""
                SELECT us.*, sp.name as plan_name, sp.features, sp.amount, sp.currency, sp.interval 
                FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
                JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
                WHERE us.user_id = %s AND us.app_id = %s AND us.status = 'active'
                ORDER BY us.created_at DESC LIMIT 1
            """, (user_id, app_id))
            
            subscription = cursor.fetchone()
            
            # If no active subscription, check for a pending one
            if not subscription:
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
            
            # If no subscription found, return the free plan
            if not subscription:
                # Auto-create free plan subscription
                free_plan_id = f"plan_free_{app_id}"
                return self.create_subscription(user_id, free_plan_id, app_id)
            
            # Parse features JSON
            if subscription and subscription.get('features'):
                subscription['features'] = parse_json_field(subscription['features'])
            
            # Parse metadata JSON
            if subscription and subscription.get('metadata'):
                subscription['metadata'] = parse_json_field(subscription['metadata'])
            
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting user subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
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
            
            # Process the plans - parse JSON fields
            for plan in plans:
                if plan.get('features'):
                    plan['features'] = parse_json_field(plan['features'])
                
                if plan.get('payment_gateways'):
                    plan['payment_gateways'] = parse_json_field(plan['payment_gateways'], ['razorpay'])
            
            cursor.close()
            conn.close()
            
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
            
            logger.info(f"Processing {provider} webhook event: {event_type}")
            
            # Log the webhook event for debugging
            entity_id = None
            user_id = None
            
            # Extract entity ID and user ID from payload
            if provider == 'razorpay':
                if 'payload' in payload:
                    if 'payment' in payload['payload']:
                        entity_id = payload['payload']['payment'].get('id')
                    elif 'subscription' in payload['payload']:
                        entity_id = payload['payload']['subscription'].get('id')
                        # Try to extract user_id from notes
                        if 'notes' in payload['payload']['subscription']:
                            user_id = payload['payload']['subscription']['notes'].get('user_id')
            
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
                if event_type == 'subscription.authenticated':
                    result = self._handle_razorpay_subscription_authenticated(payload)
                elif event_type == 'subscription.activated':
                    result = self._handle_razorpay_subscription_activated(payload)
                elif event_type == 'subscription.charged':
                    result = self._handle_razorpay_subscription_charged(payload)
                elif event_type == 'subscription.completed':
                    result = self._handle_razorpay_subscription_completed(payload)
                elif event_type == 'subscription.cancelled':
                    result = self._handle_razorpay_subscription_cancelled(payload)
                else:
                    logger.info(f"Unhandled Razorpay event type: {event_type}")
                    result = {'status': 'ignored', 'message': f'Unhandled event type: {event_type}'}
            elif provider == 'paypal':
                logger.info(f"PayPal webhook handling not fully implemented: {event_type}")
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
    
    def _handle_razorpay_subscription_authenticated(self, payload):
        """Handle subscription.authenticated webhook event"""
        try:
            # Extract subscription data
            subscription_data = payload.get('payload', {}).get('subscription', {}).get('entity', {})
            razorpay_subscription_id = subscription_data.get('id')
            
            # Debug logging
            logger.info(f"Subscription Authenticated - Subscription ID: {razorpay_subscription_id}")
            
            # Validate required fields
            if not razorpay_subscription_id:
                logger.error("No subscription ID in authenticated webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            # Connect to database
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the subscription record
            cursor.execute(f"""
                SELECT id, user_id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s
            """, (razorpay_subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                cursor.close()
                conn.close()
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Update subscription status
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = 'authenticated', 
                    updated_at = NOW(),
                    metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
                WHERE razorpay_subscription_id = %s
                    AND status != 'active';
            """, (json.dumps(subscription_data), razorpay_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Subscription authenticated: {razorpay_subscription_id}")
            return {'status': 'success', 'message': 'Subscription authenticated'}
            
        except Exception as e:
            logger.error(f"Error handling subscription authenticated: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}
    
    def _handle_razorpay_subscription_activated(self, payload):
        """Handle subscription.activated webhook event"""
        try:
            # Extract subscription data
            subscription_data = payload.get('payload', {}).get('subscription', {}).get('entity', {})
            razorpay_subscription_id = subscription_data.get('id')
            
            # Debug logging
            logger.info(f"Subscription Activated - Subscription ID: {razorpay_subscription_id}")
            
            # Validate required fields
            if not razorpay_subscription_id:
                logger.error("No subscription ID in activated webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            # Connect to database
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the subscription record
            cursor.execute(f"""
                SELECT id, user_id, plan_id, app_id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s
            """, (razorpay_subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                cursor.close()
                conn.close()
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Calculate subscription period
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
            cursor.execute(f"""
                SELECT sp.interval, sp.interval_count
                FROM {DB_TABLE_SUBSCRIPTION_PLANS} sp
                JOIN {DB_TABLE_USER_SUBSCRIPTIONS} us ON sp.id = us.plan_id
                WHERE us.razorpay_subscription_id = %s
            """, (razorpay_subscription_id,))
            
            plan_details = cursor.fetchone()
            
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
            
            # Update subscription status
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
            
            logger.info(f"Subscription activated: {razorpay_subscription_id}")
            
            # Get user ID and app ID
            user_id = subscription['user_id']
            app_id = subscription['app_id']

            # IMPORTANT: Initialize resource quota for the activated subscription
            logger.info(f"Initializing resource quota for subscription {subscription['id']}")
            quota_result = self.initialize_resource_quota(user_id, subscription['id'], app_id)
            
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
    
    def _handle_razorpay_subscription_charged(self, payload):
        """Handle subscription.charged webhook event - renews subscription and resets resources"""
        try:
            # Extract all relevant data
            subscription_data = payload.get('payload', {}).get('subscription', {})
        
            # Check if subscription data is nested inside an "entity" field
            if 'entity' in subscription_data:
                subscription_data = subscription_data.get('entity', {})
            
            invoice_data = payload.get('payload', {}).get('invoice', {})
            payment_data = payload.get('payload', {}).get('payment', {}).get('entity', {})
            
            # Get IDs
            razorpay_subscription_id = subscription_data.get('id')
            razorpay_invoice_id = payment_data.get('invoice_id') if payment_data else None
            razorpay_payment_id = payment_data.get('id') if payment_data else None
            
            # Debug logging
            logger.info(f"Subscription Charged - Subscription ID: {razorpay_subscription_id}, Invoice ID: {razorpay_invoice_id}, Payment ID: {razorpay_payment_id}")
            
            # Validate required fields
            if not razorpay_subscription_id:
                logger.error("Missing subscription ID in charged webhook")
                # Don't return - try to continue processing with available data
            
            # Connect to database
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get subscription details from DB
            cursor.execute(f"""
                SELECT id, user_id, app_id, plan_id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s
            """, (razorpay_subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                cursor.close()
                conn.close()
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Create new period dates
            new_start = datetime.now()
            
            # Get plan details
            cursor.execute(f"SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS} WHERE id = %s", (subscription['plan_id'],))
            plan = cursor.fetchone()
            
            # Calculate new period end date
            new_end = calculate_period_end(
                new_start,
                plan['interval'] if plan else 'month',
                plan['interval_count'] if plan else 1
            )
            
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
            
            # If we have invoice details, record the invoice
            if razorpay_invoice_id:
                invoice_id = generate_id('inv_')
                
                # Get invoice amount
                amount = payment_data.get('amount', 0)
                currency = payment_data.get('currency', 'INR')
                status = payment_data.get('status', 'pending')
                
                if status == 'captured':
                    status = 'Paid'
                
                # Insert invoice record
                cursor.execute(f"""
                    INSERT INTO subscription_invoices
                    (id, subscription_id, user_id, razorpay_invoice_id, 
                    amount, currency, status, payment_id, invoice_date, app_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                """, (invoice_id, subscription['id'], subscription['user_id'], 
                    razorpay_invoice_id, amount, currency, status, 
                    razorpay_payment_id, subscription['app_id']))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Subscription charged processed: {razorpay_subscription_id}")
            
            return {
                'status': 'success',
                'message': 'Subscription renewed and usage reset',
                'new_period_start': new_start.isoformat(),
                'new_period_end': new_end.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling subscription charged: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}
    
    def _handle_razorpay_subscription_completed(self, payload):
        """Handle subscription.completed webhook event"""
        subscription_data = payload.get('payload', {}).get('subscription', {}).get('entity', {})
        razorpay_subscription_id = subscription_data.get('id')
        
        if not razorpay_subscription_id:
            logger.error("No subscription ID in completed webhook")
            return {'status': 'error', 'message': 'Missing subscription ID'}
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the subscription record
            cursor.execute(f"""
                SELECT id, user_id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s
            """, (razorpay_subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                cursor.close()
                conn.close()
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Update subscription status
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = 'completed', 
                    updated_at = NOW(),
                    metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
                WHERE razorpay_subscription_id = %s
            """, (json.dumps(subscription_data), razorpay_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Subscription completed: {razorpay_subscription_id}")
            return {'status': 'success', 'message': 'Subscription marked as completed'}
            
        except Exception as e:
            logger.error(f"Error handling subscription completed: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}
    
    def _handle_razorpay_subscription_cancelled(self, payload):
        """Handle subscription.cancelled webhook event"""
        subscription_data = payload.get('payload', {}).get('subscription', {}).get('entity', {})
        razorpay_subscription_id = subscription_data.get('id')
        
        if not razorpay_subscription_id:
            logger.error("No subscription ID in cancelled webhook")
            return {'status': 'error', 'message': 'Missing subscription ID'}
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the subscription record
            cursor.execute(f"""
                SELECT id, user_id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s
            """, (razorpay_subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                cursor.close()
                conn.close()
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Update subscription status
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = 'cancelled', 
                    updated_at = NOW(),
                    metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
                WHERE razorpay_subscription_id = %s
            """, (json.dumps(subscription_data), razorpay_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
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
        logger.info(f"Scheduling cancellation of subscription {subscription_id} for user {user_id}")
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get subscription details
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE id = %s AND user_id = %s
            """, (subscription_id, user_id))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                cursor.close()
                conn.close()
                logger.error(f"Subscription not found or not owned by user: {subscription_id}")
                raise ValueError(f"Subscription not found or not owned by user")
            
            # If it's a Razorpay subscription, schedule cancellation at the end of current cycle
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
            
            # Format end date for JSON if it exists
            end_date_str = None
            if subscription.get('current_period_end'):
                if isinstance(subscription['current_period_end'], datetime):
                    end_date_str = subscription['current_period_end'].isoformat()
                else:
                    end_date_str = str(subscription['current_period_end'])
            
            conn.commit()
            cursor.close()
            conn.close()
            
            # Return the updated subscription data
            return {
                "id": subscription_id,
                "status": "active",  # Status remains active
                "cancellation_scheduled": True,  # Add this flag instead
                "end_date": end_date_str,
                "message": "Subscription will remain active until the end of the current billing period"
            }
                
        except Exception as e:
            logger.error(f"Error scheduling subscription cancellation: {str(e)}")
            logger.error(traceback.format_exc())
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
        logger.info(f"Getting billing history for user {user_id}, app {app_id}")
        
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
        logger.info(f"Manually activating subscription {subscription_id} for user {user_id}")
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the subscription record
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE razorpay_subscription_id = %s
            """, (subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription not found: {subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Get plan details
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_SUBSCRIPTION_PLANS}
                WHERE id = %s
            """, (subscription['plan_id'],))
            
            plan = cursor.fetchone()
            
            if not plan:
                logger.error(f"Plan not found for subscription {subscription['id']}")
                return {'status': 'error', 'message': 'Plan not found'}
            
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
            """, (start_date, period_end, subscription_id))
            
            
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
                    user_id,
                    'manual_activation',
                    plan['amount'],
                    'paid',
                    payment_id,
                    subscription['app_id']
                ))
            
            # Log the manual activation
            self.db.log_event(
                'manual_activation',
                subscription_id,
                user_id,
                {'payment_id': payment_id},
                provider='razorpay',
                processed=True
            )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Subscription {subscription_id} manually activated")
            return {'status': 'success', 'message': 'Subscription activated'}
            
        except Exception as e:
            logger.error(f"Error manually activating subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}
        
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
        logger.info(f"Getting resource limits for user {user_id}, app {app_id}")
        
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
        logger.info(f"Initializing resource quota for user {user_id}, subscription {subscription_id}")
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get subscription details to get billing period dates and plan features
            cursor.execute(f"""
                SELECT us.*, sp.features, sp.app_id 
                FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
                JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
                WHERE us.id = %s
            """, (subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription {subscription_id} not found")
                cursor.close()
                conn.close()
                return False
            
            # Parse features
            features_str = subscription.get('features', '{}')
            if isinstance(features_str, str):
                try:
                    features = json.loads(features_str)
                except json.JSONDecodeError:
                    features = {}
            else:
                features = features_str or {}
            
            # Set the quota based on app
            if app_id == 'marketfit':
                document_pages_quota = features.get('document_pages', 50)
                perplexity_requests_quota = features.get('perplexity_requests', 20)
                requests_quota = 0  # Not used for MarketFit
            else:  # saleswit
                document_pages_quota = 0  # Not used for SalesWit
                perplexity_requests_quota = 0  # Not used for SalesWit
                requests_quota = features.get('requests', 20)
            
            # Log quota values for debugging
            logger.info(f"Setting quota for {app_id}: doc_pages={document_pages_quota}, perplexity={perplexity_requests_quota}, requests={requests_quota}")
            
            # Make sure billing period dates are available
            if not subscription.get('current_period_start') or not subscription.get('current_period_end'):
                # Set default billing period if not available
                current_period_start = datetime.now()
                current_period_end = current_period_start + timedelta(days=30)  # Default to 30 days
                
                # Update subscription with billing period
                cursor.execute(f"""
                    UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                    SET current_period_start = %s, current_period_end = %s
                    WHERE id = %s
                """, (current_period_start, current_period_end, subscription_id))
                
                conn.commit()
            else:
                current_period_start = subscription['current_period_start']
                current_period_end = subscription['current_period_end']
            
            # Check if there's an existing quota record for this billing period
            cursor.execute(f"""
                SELECT id FROM {DB_TABLE_RESOURCE_USAGE}
                WHERE user_id = %s AND subscription_id = %s AND app_id = %s
                AND NOW() BETWEEN billing_period_start AND billing_period_end
            """, (user_id, subscription_id, app_id))
            
            quota_record = cursor.fetchone()
            
            if quota_record:
                # Update existing record - reset quotas
                cursor.execute(f"""
                    UPDATE {DB_TABLE_RESOURCE_USAGE}
                    SET document_pages_quota = %s,
                        perplexity_requests_quota = %s,
                        requests_quota = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    document_pages_quota,
                    perplexity_requests_quota,
                    requests_quota,
                    quota_record['id']
                ))
                logger.info(f"Updated existing quota record {quota_record['id']}")
            else:
                # Create a new record
                cursor.execute(f"""
                    INSERT INTO {DB_TABLE_RESOURCE_USAGE}
                    (user_id, subscription_id, app_id, billing_period_start, billing_period_end,
                    document_pages_quota, perplexity_requests_quota, requests_quota)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id,
                    subscription_id,
                    app_id,
                    current_period_start,
                    current_period_end,
                    document_pages_quota,
                    perplexity_requests_quota,
                    requests_quota
                ))
                logger.info(f"Created new quota record for subscription {subscription_id}")
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
            
        except Exception as e:
            logger.error(f"Error initializing resource quota: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def get_resource_quota(self, user_id, app_id):
        """
        Get remaining resource quota for a user in the current billing period
        
        Args:
            user_id: The user's ID
            app_id: The application ID
            
        Returns:
            dict: Resource quota
        """
        logger.info(f"Getting resource quota for user {user_id}, app {app_id}")
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Initialize quota object based on app
            if app_id == 'marketfit':
                quota = {
                    'document_pages': 0,
                    'perplexity_requests': 0
                }
            else:  # saleswit
                quota = {
                    'requests': 0
                }
            
            # Get the user's active subscription
            cursor.execute(f"""
                SELECT id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE user_id = %s AND app_id = %s AND status = 'active'
                ORDER BY current_period_end DESC LIMIT 1
            """, (user_id, app_id))
            
            subscription_result = cursor.fetchone()
            
            # if not subscription_result:
            #     cursor.close()
            #     conn.close()
            #     return quota
            
            subscription_id = subscription_result['id']
            
            # Get quota from the tracking table for the current billing period
            if app_id == 'marketfit':
                cursor.execute(f"""
                    SELECT document_pages_quota, perplexity_requests_quota
                    FROM {DB_TABLE_RESOURCE_USAGE}
                    WHERE user_id = %s AND subscription_id = %s AND app_id = %s
                    AND NOW() BETWEEN billing_period_start AND billing_period_end
                    ORDER BY billing_period_start DESC LIMIT 1
                """, (user_id, subscription_id, app_id))
            else:  # saleswit
                cursor.execute(f"""
                    SELECT requests_quota
                    FROM {DB_TABLE_RESOURCE_USAGE}
                    WHERE user_id = %s AND subscription_id = %s AND app_id = %s
                    AND NOW() BETWEEN billing_period_start AND billing_period_end
                    ORDER BY billing_period_start DESC LIMIT 1
                """, (user_id, subscription_id, app_id))
            
            quota_result = cursor.fetchone()
            
            if quota_result:
                # Update quota with remaining values
                if app_id == 'marketfit':
                    quota['document_pages'] = quota_result['document_pages_quota']
                    quota['perplexity_requests'] = quota_result['perplexity_requests_quota']
                else:  # saleswit
                    quota['requests'] = quota_result['requests_quota']
            
            cursor.close()
            conn.close()
            
            return quota
            
        except Exception as e:
            logger.error(f"Error getting resource quota: {str(e)}")
            logger.error(traceback.format_exc())
            # Return empty quota on error
            if app_id == 'marketfit':
                return {
                    'document_pages': 0,
                    'perplexity_requests': 0
                }
            else:  # saleswit
                return {
                    'requests': 0
                }

    def check_resource_availability(self, user_id, app_id, resource_type, count=1):
        """
        Check if user has enough resources available
        
        Args:
            user_id: The user's ID
            app_id: The application ID
            resource_type: The type of resource (document_pages, perplexity_requests for marketfit, requests for saleswit)
            count: Amount to check for
            
        Returns:
            bool: True if resource is available, False otherwise
        """
        logger.info(f"Checking {resource_type} availability for user {user_id}, app {app_id}, count {count}")
        
        try:
            # Get the user's resource quota
            quota = self.get_resource_quota(user_id, app_id)
            
            # Check if the quota is enough for the requested count
            if resource_type in quota:
                return quota[resource_type] >= count
            
            # If resource type not found in quota, assume unavailable
            return False
                
        except Exception as e:
            logger.error(f"Error checking resource availability: {str(e)}")
            logger.error(traceback.format_exc())
            # Default to not available on error
            return False

    def decrement_resource_quota(self, user_id, app_id, resource_type, count=1):
        """
        Decrement resource quota for a user
        
        Args:
            user_id: The user's ID
            app_id: The application ID
            resource_type: The type of resource (document_pages, perplexity_requests for marketfit, requests for saleswit)
            count: Amount to decrement by
            
        Returns:
            bool: Success status
        """
        logger.info(f"Decrementing {resource_type} quota for user {user_id}, app {app_id} by {count}")
        
        try:
            # Check if resource is available
            if not self.check_resource_availability(user_id, app_id, resource_type, count):
                logger.warning(f"Resource {resource_type} not available for user {user_id}")
                return False
            
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the user's active subscription
            cursor.execute(f"""
                SELECT id FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE user_id = %s AND app_id = %s AND status = 'active'
                ORDER BY current_period_end DESC LIMIT 1
            """, (user_id, app_id))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                # No active subscription found
                cursor.close()
                conn.close()
                return False
            
            # Check if there's an existing quota record for this billing period
            cursor.execute(f"""
                SELECT id FROM {DB_TABLE_RESOURCE_USAGE}
                WHERE user_id = %s AND subscription_id = %s AND app_id = %s
                AND NOW() BETWEEN billing_period_start AND billing_period_end
            """, (user_id, subscription['id'], app_id))
            
            quota_record = cursor.fetchone()
            
            if not quota_record:
                # No quota record found, initialize it first
                cursor.close()
                conn.close()
                self.initialize_resource_quota(user_id, subscription['id'], app_id)
                
                # Try again with initialized quota
                return self.decrement_resource_quota(user_id, app_id, resource_type, count)
            
            # Update the quota by decrementing the specified resource
            column_name = f"{resource_type}_quota"
            cursor.execute(f"""
                UPDATE {DB_TABLE_RESOURCE_USAGE}
                SET {column_name} = GREATEST(0, {column_name} - %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (count, quota_record['id']))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
        
        except Exception as e:
            logger.error(f"Error decrementing resource quota: {str(e)}")
            logger.error(traceback.format_exc())
            return False
        
    # service.py - Add method to reset quota on subscription renewal
    def reset_quota_on_renewal(self, subscription_id):
        """
        Reset resource quota when a subscription is renewed
        
        Args:
            subscription_id: The subscription ID
            
        Returns:
            bool: Success status
        """
        logger.info(f"Resetting quota for subscription {subscription_id}")
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get subscription details
            cursor.execute(f"""
                SELECT us.*, sp.features, sp.app_id 
                FROM {DB_TABLE_USER_SUBSCRIPTIONS} us
                JOIN {DB_TABLE_SUBSCRIPTION_PLANS} sp ON us.plan_id = sp.id
                WHERE us.id = %s
            """, (subscription_id,))
            
            subscription = cursor.fetchone()
            
            if not subscription:
                logger.error(f"Subscription {subscription_id} not found")
                cursor.close()
                conn.close()
                return False
            
            # Parse features
            features = parse_json_field(subscription.get('features', '{}'))
            app_id = subscription.get('app_id')
            user_id = subscription.get('user_id')
            
            # Set the quota based on app
            if app_id == 'marketfit':
                document_pages_quota = features.get('document_pages', 50)
                perplexity_requests_quota = features.get('perplexity_requests', 20)
                requests_quota = 0  # Not used for MarketFit
            else:  # saleswit
                document_pages_quota = 0  # Not used for SalesWit
                perplexity_requests_quota = 0  # Not used for SalesWit
                requests_quota = features.get('requests', 20)
            
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
                user_id,
                subscription_id,
                app_id,
                subscription['current_period_start'],
                subscription['current_period_end'],
                document_pages_quota,
                perplexity_requests_quota,
                requests_quota,
                document_pages_quota,
                perplexity_requests_quota,
                requests_quota
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
            
        except Exception as e:
            logger.error(f"Error resetting quota on renewal: {str(e)}")
            logger.error(traceback.format_exc())
            return False
        


# service.py - Add method to get subscription by gateway ID

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
                if subscription.get('features'):
                    subscription['features'] = parse_json_field(subscription['features'])
                
                if subscription.get('metadata'):
                    subscription['metadata'] = parse_json_field(subscription['metadata'])
            
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting subscription by gateway ID: {str(e)}")
            logger.error(traceback.format_exc())
            return None
        


payment_service = PaymentService()