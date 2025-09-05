"""
Main payment service class for payment gateway operations
"""
import json
import logging
import traceback
import os
from datetime import datetime, timedelta, timezone
from .base_subscription_service import BaseSubscriptionService
from .db import DatabaseManager
from .providers.razorpay_provider import RazorpayProvider
from .providers.paypal_provider import PayPalProvider
from .utils.helpers import generate_id, calculate_period_end, calculate_billing_cycle_info, calculate_resource_utilization, calculate_advanced_proration,parse_json_field
from .config import setup_logging, DB_TABLE_SUBSCRIPTION_PLANS, DB_TABLE_USER_SUBSCRIPTIONS, DB_TABLE_RESOURCE_USAGE
logger = logging.getLogger('payment_gateway')

class PaymentService(BaseSubscriptionService):
    """
    Service class to handle payment-related operations.
    This service is designed to work across multiple applications.
    """
    
    def __init__(self, app=None, db_config=None):
        """Initialize the payment service"""
        # Initialize base service
        super().__init__(db_config)

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

    def create_subscription(self, user_id, plan_id, app_id, preferred_gateway=None):
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
        logger.info(f"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAaaaaaaaa")

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
                return self._handle_paid_subscription(user_id, plan_id, app_id, plan, existing_subscription, preferred_gateway)
                
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise


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

    def _handle_paid_subscription(self, user_id, plan_id, app_id, plan, existing_subscription, preferred_gateway=None):
        """Handle paid subscription creation"""
        try:
            # Phase 1: Get user info (separate connection)
            user = self._get_user_info(user_id)
            if not user:
                raise ValueError(f"User with ID {user_id} not found")
            
            # Phase 2: Create gateway subscription (outside database transaction)
            gateway_response = self._create_gateway_subscription(plan, user, app_id, preferred_gateway)
            
            # Phase 3: Save to database (focused transaction)
            return self._save_paid_subscription(user_id, plan_id, app_id, gateway_response)
            
        except Exception as e:
            logger.error(f"Error creating paid subscription: {str(e)}")
            raise

    def _create_gateway_subscription(self, plan, user, app_id, preferred_gateway=None):
        """Create subscription with payment gateway (no database operations)"""
        try:
            # Get payment gateways from plan
            payment_gateways = parse_json_field(plan.get('payment_gateways'), ['razorpay'])
            
            # Determine which gateway to use
            if preferred_gateway and (preferred_gateway in payment_gateways or plan.get('currency') != 'INR'):
                # Use preferred gateway if provided and valid
                gateway = preferred_gateway
            else:
                # Use first in list or default based on currency
                if plan.get('currency') == 'INR':
                    gateway = 'razorpay'  # Always use Razorpay for INR
                else:  # USD
                    gateway = payment_gateways[0] if payment_gateways else 'paypal'
            
            logger.info(f"Using payment gateway: {gateway} for subscription")
            
            if gateway == 'razorpay':
                gateway_plan_id = plan.get('razorpay_plan_id')
                
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
                # **CHANGE 1: Get the plan record to extract internal plan ID**
                plan = self._get_plan(plan_id)
                if not plan:
                    raise ValueError(f"Plan {plan_id} not found")
                
                internal_plan_id = plan['id']  # **NEW LINE: Extract internal database plan ID**
                
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
                    internal_plan_id,  # **CHANGE 2: Use internal_plan_id instead of plan_id**
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
    
    def _extract_webhook_ids(self, payload, provider):
        """Extract entity ID and user ID from webhook payload"""
        entity_id = None
        user_id = None
        
        if provider == 'razorpay':
            # For subscription events, use existing extraction method
            if 'subscription' in payload.get('payload', {}):
                subscription_data = self._extract_subscription_data(payload)
                entity_id = subscription_data.get('id')
                
                # Extract user_id from notes or database lookup
                if 'notes' in subscription_data and subscription_data['notes']:
                    user_id = subscription_data['notes'].get('user_id')
                else:
                    # Fallback to database lookup using existing method
                    subscription = self._get_subscription_by_razorpay_id(entity_id)
                    if subscription:
                        user_id = subscription.get('user_id')
            
            # For payment events
            elif 'payment' in payload.get('payload', {}):
                payment_data = payload.get('payload', {}).get('payment', {}).get('entity', {})
                entity_id = payment_data.get('id')
                
                # Try notes first, then subscription lookup
                if 'notes' in payment_data and payment_data['notes']:
                    user_id = payment_data['notes'].get('user_id')
                elif 'subscription_id' in payment_data:
                    subscription = self._get_subscription_by_razorpay_id(payment_data['subscription_id'])
                    if subscription:
                        user_id = subscription.get('user_id')
        
        return entity_id, user_id

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
        """Handle subscription.activated webhook event with invoice creation"""
        try:
            # Extract subscription data
            subscription_data = self._extract_subscription_data(payload)
            razorpay_subscription_id = subscription_data.get('id')
            
            logger.info(f"Subscription Activated - Subscription ID: {razorpay_subscription_id}")
            
            if not razorpay_subscription_id:
                logger.error("No subscription ID in activated webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            # Get subscription from database
            subscription = self._get_subscription_by_razorpay_id(razorpay_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for Razorpay ID: {razorpay_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Calculate period dates
            start_date, period_end = self._calculate_subscription_period(subscription_data, subscription['plan_id'])
            
            # Update subscription
            self._activate_subscription_with_period(razorpay_subscription_id, start_date, period_end, subscription_data)
            
            # Initialize resource quota
            quota_result = self.initialize_resource_quota(
                subscription['user_id'], 
                subscription['id'], 
                subscription['app_id']
            )
            
            if not quota_result:
                logger.error(f"Failed to initialize resource quota for subscription {subscription['id']}")
            
            # AFTER resource initialization, extract payment data for invoice
            payment_data = payload.get('payload', {}).get('payment', {}).get('entity', {})
            payment_method = payment_data.get('method')  # 'card', 'upi', 'netbanking', etc.
            payment_id = payment_data.get('id')
            razorpay_invoice_id = payment_data.get('invoice_id')  # Extract Razorpay invoice ID
            payment_amount = payment_data.get('amount', 0) / 100  # Convert paisa to rupees
            payment_currency = payment_data.get('currency', 'INR')
            
            logger.info(f"Payment details: ID={payment_id}, Invoice ID={razorpay_invoice_id}, Method={payment_method}, Amount={payment_amount}")
            
            # Create invoice for the initial payment
            try:
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                # Check if invoice already exists with this payment ID
                cursor.execute("""
                    SELECT id FROM subscription_invoices 
                    WHERE razorpay_payment_id = %s OR razorpay_invoice_id = %s
                """, (payment_id, razorpay_invoice_id))
                
                existing_invoice = cursor.fetchone()
                
                if not existing_invoice:
                    # Create the invoice record
                    from .utils.helpers import generate_id
                    invoice_id = generate_id('inv_')
                    
                    cursor.execute("""
                        INSERT INTO subscription_invoices
                        (id, subscription_id, user_id, razorpay_payment_id, razorpay_invoice_id, amount, currency,
                        status, payment_method, invoice_date, paid_at, app_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s)
                    """, (
                        invoice_id,
                        subscription['id'],
                        subscription['user_id'],
                        payment_id,
                        razorpay_invoice_id,
                        payment_amount,
                        payment_currency,
                        'paid',
                        payment_method or 'unknown',
                        subscription['app_id']
                    ))
                    
                    conn.commit()
                    logger.info(f"Created invoice {invoice_id} for subscription activation {razorpay_subscription_id}, Razorpay Invoice ID: {razorpay_invoice_id}")
                else:
                    logger.info(f"Invoice already exists for payment {payment_id}, skipping creation")
                
                cursor.close()
                conn.close()
                    
            except Exception as e:
                logger.error(f"Error creating invoice for subscription activation: {str(e)}")
                # Continue with other operations even if invoice creation fails

            return {
                'status': 'success', 
                'message': 'Subscription activated with resources and invoice',
                'period_start': start_date.isoformat(),
                'period_end': period_end.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling subscription activated: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}
        
    def _handle_razorpay_subscription_charged(self, subscription_data, payment_data):
        """Handle subscription.charged webhook event with plan change and payment method detection"""
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
            
            database_plan_id = subscription['plan_id']  # This is already the internal plan ID
            
            # Check if this is a fresh subscription (recent activation) to prevent duplicates
            cursor.execute("""
                SELECT updated_at FROM user_subscriptions
                WHERE id = %s AND updated_at > DATE_SUB(NOW(), INTERVAL 5 MINUTE)
            """, (subscription['id'],))
            
            recent_subscription_update = cursor.fetchone()
            is_fresh_subscription = recent_subscription_update is not None
            
            if is_fresh_subscription:
                logger.info("Skipping subscription.charged processing - fresh subscription already handled by activation webhook")
                cursor.close()
                conn.close()
                return {
                    'success': True,
                    'subscription_id': subscription['id'],
                    'message': 'Fresh subscription - processed by activation webhook',
                    'skipped': True
                }
            
            # This is a renewal - proceed with full processing
            logger.info(f"Processing renewal/update for subscription: {subscription['id']}")
            
            # Flags to prevent duplicate execution
            plan_changed = False
            resource_quota_handled = False
            
            # FIXED: DETECT PLAN CHANGE (Optimized - no redundant database call)
            if webhook_plan_id:
                # Get the plan record for webhook plan ID to get its internal ID
                webhook_plan = self._get_plan(webhook_plan_id)
                
                if webhook_plan:
                    webhook_internal_id = webhook_plan['id']
                    
                    # Compare webhook internal ID with database internal ID directly
                    if webhook_internal_id != database_plan_id:
                        plan_changed = True
                        logger.info(f"Plan change detected: {database_plan_id} → {webhook_internal_id} ({webhook_plan['name']})")
                        
                        # Update subscription plan in database
                        cursor.execute(f"""
                            UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                            SET plan_id = %s, updated_at = NOW()
                            WHERE id = %s
                        """, (webhook_internal_id, subscription['id']))
                        
                        # Reset resource quota to new plan
                        self._reset_quota_for_plan_change(
                            subscription['user_id'], 
                            subscription['id'],
                            webhook_plan,
                            subscription['app_id']
                        )
                        resource_quota_handled = True
                        
                        logger.info(f"Plan change synced: User {subscription['user_id']} moved to {webhook_plan['name']}")
                    else:
                        logger.debug(f"No plan change detected: same plan ID {database_plan_id}")
                else:
                    logger.warning(f"Webhook plan {webhook_plan_id} not found in database")
            
            # DETECT PAYMENT METHOD CHANGE (existing logic)
            current_payment_method = payment_data.get('method')
            
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
            
            # Extract payment data
            payment_id = payment_data.get('id')
            razorpay_invoice_id = payment_data.get('invoice_id')
            amount = payment_data.get('amount', 0) / 100  # Convert paisa to rupees
            currency = payment_data.get('currency', 'INR')
            
            # Check if invoice already exists with this payment ID
            cursor.execute("""
                SELECT id FROM subscription_invoices 
                WHERE razorpay_payment_id = %s OR razorpay_invoice_id = %s
            """, (payment_id, razorpay_invoice_id))
            
            existing_invoice = cursor.fetchone()
            
            if not existing_invoice:
                # Create invoice record for this payment only if it doesn't exist
                invoice_id = generate_id('inv_')
                
                cursor.execute("""
                    INSERT INTO subscription_invoices 
                    (id, subscription_id, user_id, razorpay_payment_id, razorpay_invoice_id, amount, currency, 
                    status, payment_method, invoice_date, app_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                """, (
                    invoice_id,
                    subscription['id'],
                    subscription['user_id'],
                    payment_id,
                    razorpay_invoice_id,
                    amount,
                    currency,
                    'paid',
                    current_payment_method or 'unknown',
                    subscription['app_id']
                ))
                
                logger.info(f"Created invoice {invoice_id} for subscription charged {razorpay_sub_id}")
            else:
                logger.info(f"Invoice already exists for payment {payment_id}, skipping creation")
                invoice_id = existing_invoice['id']
            
            # Reset resource quota for the new billing period (only if not already handled by plan change)
            if not resource_quota_handled:
                self.initialize_resource_quota(
                    subscription['user_id'], 
                    subscription['id'], 
                    subscription['app_id']
                )
            
            # Get plan details for proper interval calculation
            current_plan = self._get_plan(database_plan_id)
            
            if current_plan:
                interval = current_plan['interval']
                interval_count = current_plan['interval_count']
                
                # Calculate proper interval for SQL
                if interval == 'month':
                    sql_interval = f"INTERVAL {interval_count} MONTH"
                elif interval == 'year':
                    sql_interval = f"INTERVAL {interval_count} YEAR"
                else:
                    # Fallback to monthly
                    sql_interval = "INTERVAL 1 MONTH"
                    logger.warning(f"Unknown interval '{interval}' for subscription {subscription['id']}, defaulting to monthly")
            else:
                # Fallback if plan not found
                sql_interval = "INTERVAL 1 MONTH"
                logger.warning(f"Plan details not found for subscription {subscription['id']}, defaulting to monthly")
            
            # Update subscription billing dates for renewal with proper interval
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET current_period_start = NOW(),
                    current_period_end = DATE_ADD(NOW(), {sql_interval}),
                    status = 'active'
                WHERE id = %s
            """, (subscription['id'],))
            
            logger.info(f"Updated billing period for renewal with {sql_interval}")
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Subscription charged processed: {subscription['user_id']} - ₹{amount}")
            
            return {
                'success': True,
                'subscription_id': subscription['id'],
                'invoice_id': invoice_id,
                'amount': amount,
                'payment_method': current_payment_method,
                'plan_changed': plan_changed
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
            
            # Calculate proper billing period end based on new plan
            interval = new_plan.get('interval', 'month')
            interval_count = new_plan.get('interval_count', 1)
            
            # Calculate proper interval for SQL
            if interval == 'month':
                sql_interval = f"INTERVAL {interval_count} MONTH"
            elif interval == 'year':
                sql_interval = f"INTERVAL {interval_count} YEAR"
            else:
                # Fallback to monthly
                sql_interval = "INTERVAL 1 MONTH"
                logger.warning(f"Unknown interval '{interval}' for plan {new_plan.get('id')}, defaulting to monthly")
            
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), DATE_ADD(NOW(), {sql_interval}))
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
            
            logger.info(f"Resource quota reset for plan change: {user_id} → {new_plan['name']} with {sql_interval}")
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
                    # Check if invoice already exists for this payment
                    cursor.execute("""
                        SELECT id FROM subscription_invoices 
                        WHERE razorpay_payment_id = %s
                    """, (payment_id,))
                    
                    existing_invoice = cursor.fetchone()
                    
                    if not existing_invoice:
                        invoice_id = generate_id('inv_')
                        razorpay_invoice_id = f'manual_activation_{payment_id}'  # More descriptive
                        
                        cursor.execute("""
                            INSERT INTO subscription_invoices
                            (id, subscription_id, user_id, razorpay_payment_id, razorpay_invoice_id, amount, status, 
                            payment_id, app_id, invoice_date, paid_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        """, (
                            invoice_id, 
                            subscription['id'],
                            subscription['user_id'],
                            payment_id,
                            razorpay_invoice_id,  # Using descriptive invoice ID
                            plan['amount'],
                            'paid',
                            payment_id,
                            subscription['app_id']
                        ))
                    else:
                        logger.info(f"Invoice already exists for payment {payment_id}, skipping creation")
                
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
            webhook_plan_id = subscription_data.get('plan_id')  # Razorpay plan ID
            status = subscription_data.get('status')
            
            update_fields = []
            update_values = []
            
            # FIXED: Handle plan_id properly
            if webhook_plan_id:
                # Resolve Razorpay plan ID to internal database plan ID
                plan = self._get_plan(webhook_plan_id)
                if plan:
                    update_fields.append("plan_id = %s")
                    update_values.append(plan['id'])  # ← FIXED: Use internal plan ID
                    logger.info(f"Webhook plan update: {webhook_plan_id} → {plan['id']}")
                else:
                    logger.warning(f"Plan {webhook_plan_id} not found, skipping plan update")
            
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
            # Fix: Extract both subscription_data and payment_data from payload
            subscription_data = self._extract_charged_subscription_data(payload)
            payment_data = payload.get('payload', {}).get('payment', {}).get('entity', {})
            return self._handle_razorpay_subscription_charged(subscription_data, payment_data)
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
        elif event_type == 'payment_link.paid':     # NEW
            return self._handle_razorpay_payment_link_paid(payload)
        elif event_type == 'payment.captured':      # NEW
            return self._handle_razorpay_payment_captured(payload)
        elif event_type == 'invoice.paid':  # ADD THIS LINE
            return self._handle_razorpay_invoice_paid(payload)  # ADD THIS LINE
        else:
            return {'status': 'ignored', 'message': f'Unhandled event type: {event_type}'}

    def process_webhook_event(self, provider, event_type, event_id, payload):
        """
        Centralized webhook event processing - replaces handle_webhook()
        All webhook business logic happens here
        """
        try:
            # Extract entity and user IDs for logging
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
            
            # Route to provider-specific handler
            if provider == 'razorpay':
                result = self._handle_razorpay_webhook(event_type, payload)
            elif provider == 'paypal':
                # Keep existing PayPal logic
                result = {'status': 'success', 'message': f'PayPal event {event_type} processed'}
            else:
                result = {'success': False, 'message': f'Unknown provider: {provider}'}
            
            # Mark event as processed
            self.db.mark_event_processed(event_id, provider)
            
            # Log completion
            self.db.log_event(
                f"{event_type}_processed",
                entity_id,
                user_id,
                result,
                provider=provider,
                processed=True
            )
            
            return {'success': True, 'message': f'Processed {event_type} event', 'result': result}
            
        except Exception as e:
            logger.error(f"Error processing webhook event: {str(e)}")
            logger.error(traceback.format_exc())
            return {'success': False, 'message': str(e)}

    def _check_existing_invoice(self, payment_id, razorpay_invoice_id):
        """Check if an invoice already exists for a payment ID or Razorpay invoice ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT id, subscription_id FROM subscription_invoices 
                WHERE razorpay_payment_id = %s OR razorpay_invoice_id = %s
            """, (payment_id, razorpay_invoice_id))
            
            existing_invoice = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return existing_invoice
        except Exception as e:
            logger.error(f"Error checking existing invoice: {str(e)}")
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()
            return None

    def _handle_razorpay_payment_captured(self, payload):
        """Handle payment.captured webhook - with specific handling + fallback"""
        try:
            payment_data = payload.get('payload', {}).get('payment', {}).get('entity', {})
            
            payment_id = payment_data.get('id')
            payment_amount = payment_data.get('amount', 0) / 100
            payment_currency = payment_data.get('currency', 'INR')
            payment_method = payment_data.get('method')
            razorpay_invoice_id = payment_data.get('invoice_id') or f'payment_{payment_id}'
            
            # Handle notes properly
            notes = payment_data.get('notes', {})
            if isinstance(notes, list) or notes is None:
                notes = {}
            
            logger.info(f"Processing payment captured: ID={payment_id}")
            
            # Early return if invoice exists
            existing_invoice = self._check_existing_invoice(payment_id, razorpay_invoice_id)
            if existing_invoice:
                return {'status': 'success', 'message': 'Invoice already exists'}
            
            subscription_id = notes.get('subscription_id')
            payment_type = notes.get('payment_type')
            
            # Specific handling for known payment types
            if subscription_id and payment_type == 'excess_consumption':
                return self._process_excess_consumption_payment(payment_id, subscription_id, payment_data)
            
            # ✅ KEEP THIS FALLBACK - Generic handling for any payment with subscription_id
            if subscription_id:
                logger.info(f"Creating invoice for subscription payment: {payment_id}, type: {payment_type or 'unknown'}")
                invoice_id = self._create_simple_invoice(
                    payment_id,
                    razorpay_invoice_id,
                    subscription_id,
                    payment_amount,
                    payment_currency,
                    payment_method
                )
                
                if invoice_id:
                    return {
                        'status': 'success',
                        'message': f'Invoice created for {payment_type or "subscription"} payment',
                        'invoice_id': invoice_id
                    }
            
            # No subscription_id found
            logger.info(f"Payment {payment_id} has no subscription context - subscription webhooks will handle")
            return {
                'status': 'success',
                'message': 'Payment processed - subscription webhooks handle regular subscription invoices',
                'payment_id': payment_id
            }
            
        except Exception as e:
            logger.error(f"Error handling payment captured: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _handle_razorpay_payment_link_paid(self, payload):
        """Handle payment_link.paid webhook event - with specific handling + fallback"""
        try:
            payment_link_data = payload.get('payload', {}).get('payment_link', {}).get('entity', {})
            payment_data = payload.get('payload', {}).get('payment', {}).get('entity', {})
            
            payment_id = payment_data.get('id')
            payment_amount = payment_data.get('amount', 0) / 100  # Convert paisa to rupees
            payment_currency = payment_data.get('currency', 'INR')
            payment_method = payment_data.get('method')
            razorpay_invoice_id = payment_data.get('invoice_id') or f'payment_link_{payment_id}'
            
            # Extract payment link notes
            notes = payment_link_data.get('notes', {})
            if isinstance(notes, list) or notes is None:
                notes = {}
            
            logger.info(f"Processing payment link paid: ID={payment_id}")
            
            # Check if invoice already exists - EARLY RETURN
            existing_invoice = self._check_existing_invoice(payment_id, razorpay_invoice_id)
            if existing_invoice:
                logger.info(f"Invoice already exists for payment {payment_id}, skipping creation")
                return {
                    'status': 'success',
                    'message': 'Invoice already exists',
                    'invoice_id': existing_invoice['id']
                }
            
            subscription_id = notes.get('subscription_id')
            payment_type = notes.get('payment_type')
            
            # Specific handling for known payment types
            if subscription_id and payment_type == 'excess_consumption':
                return self._process_excess_consumption_payment(payment_id, subscription_id, payment_data)
            
            # Generic handling for any payment with subscription_id (fallback)
            if subscription_id:
                logger.info(f"Creating invoice for subscription payment link: {payment_id}, type: {payment_type or 'unknown'}")
                invoice_id = self._create_simple_invoice(
                    payment_id,
                    razorpay_invoice_id,
                    subscription_id,
                    payment_amount,
                    payment_currency,
                    payment_method
                )
                
                if invoice_id:
                    return {
                        'status': 'success',
                        'message': f'Invoice created for {payment_type or "subscription"} payment link',
                        'invoice_id': invoice_id
                    }
            
            # No subscription_id - standalone payment link (invoices, bookings, etc.)
            logger.info(f"Payment link {payment_id} is standalone - no subscription context")
            return {
                'status': 'success',
                'message': 'Standalone payment link processed',
                'payment_id': payment_id
            }
            
        except Exception as e:
            logger.error(f"Error handling payment link paid: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _process_excess_consumption_payment(self, payment_id, subscription_id, payment_data):
        """Process excess consumption payment - simplified without duplicate checks"""
        try:
            # Process the payment directly (no need to check existing_invoice again)
            result = self.handle_additional_payment_completion(payment_id, subscription_id)
            
            # Create invoice to mark as processed
            payment_amount = payment_data.get('amount', 0) / 100
            payment_currency = payment_data.get('currency', 'INR')
            payment_method = payment_data.get('method')
            razorpay_invoice_id = payment_data.get('invoice_id')
            
            invoice_id = self._create_simple_invoice(
                payment_id, 
                razorpay_invoice_id,
                subscription_id,
                payment_amount,
                payment_currency,
                payment_method
            )
            
            return {
                'status': 'success',
                'message': 'Excess consumption payment processed',
                'result': result,
                'invoice_id': invoice_id
            }
        except Exception as e:
            logger.error(f"Error processing excess consumption payment: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _create_simple_invoice(self, payment_id, razorpay_invoice_id, subscription_id, 
                          amount, currency, payment_method):
        """Create a simple invoice without metadata"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get subscription details
            cursor.execute("""
                SELECT user_id, app_id FROM user_subscriptions
                WHERE id = %s
            """, (subscription_id,))
            
            sub_info = cursor.fetchone()
            if not sub_info:
                logger.error(f"Subscription {subscription_id} not found")
                cursor.close()
                conn.close()
                return None
            
            # Create invoice
            invoice_id = generate_id('inv_')
            
            cursor.execute("""
                INSERT INTO subscription_invoices
                (id, subscription_id, user_id, razorpay_payment_id, razorpay_invoice_id, 
                amount, currency, status, payment_method, invoice_date, paid_at, app_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s)
            """, (
                invoice_id,
                subscription_id,
                sub_info['user_id'],
                payment_id,
                razorpay_invoice_id,
                amount,
                currency,
                'paid',
                payment_method or 'unknown',
                sub_info['app_id']
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Created invoice {invoice_id} for excess consumption payment {payment_id}")
            return invoice_id
            
        except Exception as e:
            logger.error(f"Error creating simple invoice: {str(e)}")
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()
            return None


        
    def _get_subscription_payment_method(self, subscription):
        """Detect payment method from database records and metadata"""
        try:
            subscription_id = subscription['id']
            
            # PRIMARY CHECK: Look for payment_method directly in metadata
            metadata = subscription.get('metadata', {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}
                    
            # The payment_method exists directly at the root level of metadata
            if metadata and metadata.get('payment_method'):
                payment_method = metadata.get('payment_method')
                logger.info(f"[UPGRADE] Payment method found in metadata: {payment_method}")
                
                payment_method_lower = str(payment_method).lower()
                if 'upi' in payment_method_lower:
                    logger.info(f"[UPGRADE] UPI payment method detected from metadata")
                    return 'upi'
                elif 'card' in payment_method_lower:
                    return 'card'
                else:
                    return 'other'
            
            # FALLBACK: Check subscription_invoices table (existing logic)
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
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
                logger.info(f"[UPGRADE] Payment method from invoice database: {payment_method}")
                
                payment_method_lower = str(payment_method).lower()
                if 'upi' in payment_method_lower:
                    return 'upi'
                elif 'card' in payment_method_lower:
                    return 'card'
                else:
                    return 'other'
            
            # No payment method found
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
            remaining_values = self._calculate_value_remaining_percentage(billing_cycle_info, resource_info)
            value_remaining_pct = remaining_values['current_plan_remaining']  # Use the correct key
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
            
            response['gateway'] = 'razorpay'
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
            
            response['gateway'] = 'razorpay'
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
    
    # SUBSCRIPTION UPGRADE FUNCTIONALITY

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

            # Phase 3: Route to appropriate upgrade handler based on currency
            currency = new_plan.get('currency')

            if currency not in ['INR', 'USD']:
                raise ValueError({
                    'error': True,
                    'error_type': 'unsupported_currency',
                    'message': 'Unsupported currency. Please contact support.',
                    'action_required': 'contact_support'
                })

            logger.info(f"[UPGRADE] Currency: {currency}, Gateway: razorpay")
            
            if currency == 'INR':
                return self._handle_inr_upgrade_with_payment_method(
                    subscription, current_plan, new_plan, app_id, 
                    billing_cycle_info, resource_info
                )
            else:  # USD
                    return self._handle_usd_razorpay_upgrade(
                        subscription, current_plan, new_plan, app_id,
                        billing_cycle_info, resource_info
                    )
        except Exception as e:
            logger.error(f"[UPGRADE] Service exception: {str(e)}")
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
            
            # Get the new plan's Razorpay plan ID
            new_plan = self._get_plan(new_plan_id)
            if not new_plan:
                raise ValueError(f"Plan {new_plan_id} not found")
            
            razorpay_plan_id = new_plan.get('razorpay_plan_id')
            if not razorpay_plan_id:
                raise ValueError(f"Plan {new_plan_id} missing Razorpay plan ID")
            
            # Razorpay USD allows plan changes
            response = self.razorpay.client.subscription.edit(razorpay_subscription_id, {
                'plan_id': razorpay_plan_id
            }, timeout=30)
            
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
            # Skip simple upgrade - do direct Razorpay plan update
            razorpay_subscription_id = subscription['razorpay_subscription_id']
            new_razorpay_plan_id = new_plan['razorpay_plan_id']
            
            if not new_razorpay_plan_id:
                raise ValueError(f"Plan {new_plan['id']} missing Razorpay plan ID")
            
            # Update Razorpay subscription directly
            response = self.razorpay.client.subscription.edit(razorpay_subscription_id, {
                'plan_id': new_razorpay_plan_id
            }, timeout=30)
            
            if 'error' in response:
                raise ValueError(f"Razorpay upgrade failed: {response.get('error', {}).get('description')}")
            
            # Update local database plan
            self._update_subscription_plan(subscription['id'], new_plan['id'])
            
            # Calculate time factor for resource allocation
            time_remaining_pct = billing_cycle_info['time_factor']
            resource_remaining_pct = 1 - resource_info['base_plan_consumed_pct']
            
            # Check if additional payment is needed
            if (time_remaining_pct - resource_remaining_pct) >= 0.05:
                # Additional payment required - add temporary resources first
                self._add_temporary_resources(subscription['user_id'], subscription['id'], app_id)
                
                # Calculate additional payment
                excess_consumption_pct = (time_remaining_pct - resource_remaining_pct) - 0.05
                additional_amount = excess_consumption_pct * self._ensure_float(current_plan['amount'])
                
                # Store time factor for payment completion processing
                self._store_razorpay_annual_upgrade_metadata(
                    subscription['id'], 
                    time_remaining_pct, 
                    additional_amount
                )
                
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
                
                return {
                    'success': True,
                    'upgrade_type': 'razorpay_plan_change',
                    'subscription_id': subscription['id'],
                    'new_plan_id': new_plan['id'],
                    'additional_payment_required': True,
                    'additional_amount': additional_amount,
                    'additional_payment_link': additional_payment_result.get('short_url'),
                    'message': message,
                    'temporary_resources_added': True
                }
            else:
                # No additional payment needed - set proportional resources immediately
                self.initialize_resource_quota(
                    subscription['user_id'], 
                    subscription['id'], 
                    subscription['app_id'],
                    time_remaining_pct  # Use time factor for proportional allocation
                )
                
                return {
                    'success': True,
                    'upgrade_type': 'razorpay_plan_change',
                    'subscription_id': subscription['id'],
                    'new_plan_id': new_plan['id'],
                    'additional_payment_required': False,
                    'message': f'Subscription upgraded successfully with proportional resources ({time_remaining_pct:.1%} of annual quota).',
                    'proportional_resources_allocated': True,
                    'time_factor_used': time_remaining_pct
                }
            
        except Exception as e:
            logger.error(f"Error in annual USD Razorpay upgrade: {str(e)}")
            raise

    def _store_razorpay_annual_upgrade_metadata(self, subscription_id, time_factor, additional_amount):
        """Store Razorpay annual upgrade metadata including time factor"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            upgrade_metadata = {
                'razorpay_annual_upgrade': {
                    'time_factor': time_factor,
                    'additional_amount': additional_amount,
                    'upgrade_timestamp': datetime.now().isoformat(),
                    'additional_payment_required': additional_amount > 0
                }
            }
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (json.dumps(upgrade_metadata), subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Stored Razorpay annual upgrade metadata for subscription {subscription_id} with time factor {time_factor}")
            
        except Exception as e:
            logger.error(f"Error storing Razorpay annual upgrade metadata: {str(e)}")
            raise

    def _clear_razorpay_annual_upgrade_metadata(self, subscription_id):
        """Clear Razorpay annual upgrade metadata after completion"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET metadata = JSON_REMOVE(
                    IFNULL(metadata, '{{}}'), 
                    '$.razorpay_annual_upgrade'
                ),
                updated_at = NOW()
                WHERE id = %s
            """, (subscription_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Cleared Razorpay annual upgrade metadata for subscription {subscription_id}")
            
        except Exception as e:
            logger.error(f"Error clearing Razorpay annual upgrade metadata: {str(e)}")
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

    def handle_additional_payment_completion(self, payment_id, subscription_id):
        """Handle completion of additional payment for USD annual upgrade"""
        try:
            subscription = self._get_subscription_details(subscription_id)
            if not subscription:
                return {'error': True, 'message': 'Subscription not found'}
            
            # Get stored time factor from metadata
            metadata = subscription.get('metadata', {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}
            
            # Extract time factor from Razorpay annual upgrade metadata
            razorpay_upgrade = metadata.get('razorpay_annual_upgrade', {})
            time_factor = razorpay_upgrade.get('time_factor', 1.0)  # Default to full resources if not found
            
            # Replace temporary resources with proportional quota for new plan
            self.initialize_resource_quota(
                subscription['user_id'], 
                subscription_id, 
                subscription['app_id'],
                time_factor  # Use stored time factor for proportional allocation
            )
            
            # Clear the upgrade metadata
            self._clear_razorpay_annual_upgrade_metadata(subscription_id)
            
            self.db.log_subscription_action(
                subscription_id,
                'additional_payment_completed',
                {
                    'payment_id': payment_id,
                    'time_factor_used': time_factor,
                    'proportional_resources_allocated': True
                },
                f"user_{subscription['user_id']}"
            )
            
            return {
                'success': True,
                'message': f'Additional payment processed, proportional resources activated ({time_factor:.1%} of annual quota)',
                'time_factor_used': time_factor,
                'proportional_allocation': True
            }
            
        except Exception as e:
            logger.error(f"Error handling additional payment completion: {str(e)}")
            return {'error': True, 'message': str(e)}

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

    def cancel_subscription(self, user_id, subscription_id):
        """
        Cancel a user's subscription
        Razorpay: at the end of the billing cycle
        """
        try:
            subscription = self._get_subscription_for_cancellation(user_id, subscription_id)
            
            if subscription.get('razorpay_subscription_id'):
                result = self._cancel_razorpay_subscription(subscription)
            else:
                raise ValueError("No gateway subscription found")
            
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

    def _handle_razorpay_invoice_paid(self, payload):
        """Handle invoice.paid webhook event - only for missing invoices"""
        try:
            invoice_data = payload.get('payload', {}).get('invoice', {}).get('entity', {})
            payment_data = payload.get('payload', {}).get('payment', {}).get('entity', {})
            
            invoice_id = invoice_data.get('id')
            payment_id = payment_data.get('id')
            subscription_id = invoice_data.get('subscription_id')
            
            logger.info(f"Processing invoice paid: invoice={invoice_id}, payment={payment_id}, subscription={subscription_id}")
            
            # EARLY EXIT: If invoice already exists, don't process (prevents duplicates)
            existing_invoice = self._check_existing_invoice(payment_id, invoice_id)
            if existing_invoice:
                logger.info(f"Invoice already exists for payment {payment_id}, skipping duplicate creation")
                return {'status': 'success', 'message': 'Invoice already exists'}
            
            # Only process invoices with subscription context
            if subscription_id:
                # Find our internal subscription
                subscription = self._get_subscription_by_razorpay_id(subscription_id)
                if subscription:
                    amount = float(invoice_data.get('amount', 0)) / 100  # Convert paisa to rupees/dollars
                    currency = invoice_data.get('currency', 'INR')
                    payment_method = payment_data.get('method', 'unknown')
                    
                    logger.info(f"Creating missing invoice for subscription {subscription['id']}: ${amount} {currency}")
                    
                    # Create invoice record using existing helper method
                    internal_invoice_id = self._create_simple_invoice(
                        payment_id,
                        invoice_id,
                        subscription['id'],
                        amount,
                        currency,
                        f'{payment_method}_upgrade'
                    )
                    
                    if internal_invoice_id:
                        logger.info(f"Created missing invoice {internal_invoice_id} for upgrade payment {payment_id}")
                        return {
                            'status': 'success',
                            'message': 'Missing invoice created for subscription payment',
                            'invoice_id': internal_invoice_id,
                            'payment_type': 'missing_invoice'
                        }
                    else:
                        logger.error(f"Failed to create invoice for payment {payment_id}")
                        return {'status': 'error', 'message': 'Failed to create invoice'}
                else:
                    logger.warning(f"Subscription not found for Razorpay subscription ID: {subscription_id}")
                    return {'status': 'error', 'message': 'Subscription not found in database'}
            
            logger.info(f"Invoice {invoice_id} has no subscription context, ignoring")
            return {'status': 'ignored', 'message': 'No subscription context for invoice'}
            
        except Exception as e:
            logger.error(f"Error handling invoice paid: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

payment_service = PaymentService()

