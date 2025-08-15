"""
PayPal-specific payment service class
Handles all PayPal subscription management and webhook processing
"""
import json
import logging
import traceback
import os
from datetime import datetime, timedelta, timezone

from .base_subscription_service import BaseSubscriptionService
from .providers.paypal_provider import PayPalProvider
from .utils.helpers import generate_id, calculate_period_end, calculate_billing_cycle_info, calculate_resource_utilization, parse_json_field
from .config import setup_logging, DB_TABLE_SUBSCRIPTION_PLANS, DB_TABLE_USER_SUBSCRIPTIONS, DB_TABLE_RESOURCE_USAGE

logger = logging.getLogger('payment_gateway')

class PayPalService(BaseSubscriptionService):
    """
    PayPal-specific payment service class
    Handles PayPal subscriptions, upgrades, cancellations, and webhook processing
    """
    
    def __init__(self, app=None, db_config=None):
        """Initialize the PayPal service"""
        # Initialize base service
        super().__init__(db_config)
        
        # Initialize PayPal provider
        self.paypal = PayPalProvider()
        
        # Initialize Flask app if provided
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app context"""
        self.app = app
        logger.info("Initializing PayPalService with Flask app")
        
        # Initialize database tables
        with app.app_context():
            self.db.init_tables()

    # =============================================================================
    # SUBSCRIPTION CREATION METHODS (Moved from service.py)
    # =============================================================================

    def create_subscription(self, user_id, plan_id, app_id, customer_info=None):
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

    def activate_subscription(self, subscription_id):
        """Activate PayPal subscription after user approval"""
        try:
            subscription = self._get_subscription_details(subscription_id)
            if not subscription:
                return {'error': True, 'message': 'Subscription not found'}
            
            # Update subscription status to active
            self._update_subscription_status_by_id(subscription_id, 'active')
            
            # Initialize resource quota
            quota_result = self.initialize_resource_quota(
                subscription['user_id'], 
                subscription_id, 
                subscription['app_id']
            )
            
            if not quota_result:
                logger.error(f"Failed to initialize resource quota for subscription {subscription_id}")
            
            self.db.log_subscription_action(
                subscription_id,
                'paypal_subscription_activated',
                {'subscription_id': subscription_id},
                f"user_{subscription['user_id']}"
            )
            
            return {
                'success': True,
                'subscription_id': subscription_id,
                'message': 'PayPal subscription activated successfully'
            }
            
        except Exception as e:
            logger.error(f"Error activating PayPal subscription: {str(e)}")
            return {'error': True, 'message': str(e)}

    def cancel_pending_subscription(self, subscription_id):
        """Mark pending PayPal subscription as cancelled"""
        try:
            self._update_subscription_status_by_id(subscription_id, 'cancelled')
            
            self.db.log_subscription_action(
                subscription_id,
                'paypal_subscription_cancelled_pending',
                {'subscription_id': subscription_id},
                'system'
            )
            
            return {'success': True, 'message': 'Pending subscription cancelled'}
            
        except Exception as e:
            logger.error(f"Error cancelling pending subscription: {str(e)}")
            return {'error': True, 'message': str(e)}

    # =============================================================================
    # WEBHOOK PROCESSING METHODS (New)
    # =============================================================================

    def process_webhook_event(self, provider, event_type, event_id, payload):
        """
        Process PayPal webhook events
        
        Args:
            provider: Should be 'paypal'
            event_type: PayPal event type
            event_id: Event ID for idempotency
            payload: Webhook payload
            
        Returns:
            dict: Processing result
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
            
            # Route to appropriate handler
            result = self._handle_paypal_webhook(event_type, payload)
            
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
            logger.error(f"Error processing PayPal webhook event: {str(e)}")
            logger.error(traceback.format_exc())
            return {'success': False, 'message': str(e)}

    def _handle_paypal_webhook(self, event_type, payload):
        """Route PayPal webhook events to appropriate handlers"""
        if event_type == 'BILLING.SUBSCRIPTION.CREATED':
            return self._handle_subscription_created(payload)
        elif event_type == 'BILLING.SUBSCRIPTION.ACTIVATED':
            return self._handle_subscription_activated(payload)
        elif event_type == 'PAYMENT.SALE.COMPLETED':
            return self._handle_payment_sale_completed(payload)
        elif event_type == 'BILLING.SUBSCRIPTION.PAYMENT.FAILED':
            return self._handle_subscription_payment_failed(payload)
        elif event_type == 'BILLING.SUBSCRIPTION.CANCELLED':
            return self._handle_subscription_cancelled(payload)
        elif event_type == 'BILLING.SUBSCRIPTION.SUSPENDED':
            return self._handle_subscription_suspended(payload)
        else:
            return {'status': 'ignored', 'message': f'Unhandled event type: {event_type}'}

    def _handle_subscription_created(self, payload):
        """Handle BILLING.SUBSCRIPTION.CREATED - mirror Razorpay authenticated"""
        try:
            resource = payload.get('resource', {})
            paypal_subscription_id = resource.get('id')
            
            if not paypal_subscription_id:
                logger.error("No subscription ID in created webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_paypal_id(paypal_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for PayPal ID: {paypal_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Update subscription status to created (authenticated equivalent)
            self._update_subscription_status_by_paypal_id(
                paypal_subscription_id, 
                'created', 
                resource
            )
            
            logger.info(f"PayPal subscription created: {paypal_subscription_id}")
            return {'status': 'success', 'message': 'Subscription marked as created'}
            
        except Exception as e:
            logger.error(f"Error handling subscription created: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _handle_subscription_activated(self, payload):
        """Handle BILLING.SUBSCRIPTION.ACTIVATED - mirror Razorpay activated (NO invoice)"""
        try:
            resource = payload.get('resource', {})
            paypal_subscription_id = resource.get('id')
            
            if not paypal_subscription_id:
                logger.error("No subscription ID in activated webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_paypal_id(paypal_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for PayPal ID: {paypal_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Calculate subscription period
            start_date, period_end = self._calculate_subscription_period_from_resource(resource, subscription['plan_id'])
            
            # Update subscription status and periods
            self._activate_subscription_with_period(paypal_subscription_id, start_date, period_end, resource)
            
            # Initialize resource quota
            quota_result = self.initialize_resource_quota(
                subscription['user_id'], 
                subscription['id'], 
                subscription['app_id']
            )
            
            if not quota_result:
                logger.error(f"Failed to initialize resource quota for subscription {subscription['id']}")
            
            # Set metadata flag for first payment detection
            self._set_first_payment_flag(subscription['id'], False)
            
            logger.info(f"PayPal subscription activated: {paypal_subscription_id}")
            return {
                'status': 'success', 
                'message': 'Subscription activated with resources (no invoice yet)',
                'period_start': start_date.isoformat(),
                'period_end': period_end.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling subscription activated: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _handle_payment_sale_completed(self, payload):
        """Handle PAYMENT.SALE.COMPLETED - smart invoice creation with context detection"""
        try:
            resource = payload.get('resource', {})
            payment_id = resource.get('id')
            
            logger.info(f"Processing PAYMENT.SALE.COMPLETED: {payment_id}")
            
            # Detect payment context
            context = self._detect_payment_context(resource)
            
            if context['type'] == 'fresh_subscription':
                return self._handle_fresh_subscription_payment(context['subscription'], resource)
                
            elif context['type'] == 'subscription_renewal':
                return self._handle_renewal_payment(context['subscription'], resource)
                
            elif context['type'] == 'upgrade_completion':
                return self._handle_upgrade_completion_payment(context['subscription'], resource)
                
            elif context['type'] == 'one_time_payment':
                return self._handle_one_time_payment(resource)
                
            else:
                logger.warning(f"Unknown payment context: {context}")
                return {'status': 'ignored', 'reason': 'unknown_payment_context'}
            
        except Exception as e:
            logger.error(f"Error handling payment sale completed: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    def _detect_payment_context(self, resource):
        """Detect payment context using billing_agreement_id and metadata flags"""
        billing_agreement_id = resource.get('billing_agreement_id')
        
        if billing_agreement_id:
            # This is a subscription payment
            subscription = self._get_subscription_by_paypal_id(billing_agreement_id)
            
            if not subscription:
                return {'type': 'unknown_subscription', 'subscription_id': billing_agreement_id}
            
            # Check for pending upgrade
            pending_upgrade = subscription.get('metadata', {})
            if isinstance(pending_upgrade, str):
                try:
                    pending_upgrade = json.loads(pending_upgrade)
                except:
                    pending_upgrade = {}
            
            if pending_upgrade.get('pending_paypal_upgrade'):
                return {
                    'type': 'upgrade_completion',
                    'subscription': subscription,
                    'upgrade_details': pending_upgrade['pending_paypal_upgrade']
                }
            
            # Check if first payment completed
            first_payment_completed = pending_upgrade.get('first_payment_completed', False)
            
            if not first_payment_completed:
                return {
                    'type': 'fresh_subscription',
                    'subscription': subscription
                }
            else:
                return {
                    'type': 'subscription_renewal',
                    'subscription': subscription
                }
        else:
            # One-time payment (addon, proration, etc.)
            return {'type': 'one_time_payment'}

    def _handle_fresh_subscription_payment(self, subscription, resource):
        """Handle first payment for subscription"""
        try:
            payment_id = resource.get('id')
            amount = float(resource.get('amount', {}).get('total', 0))
            currency = resource.get('amount', {}).get('currency', 'USD')
            
            # Create invoice for first payment
            invoice_id = self._create_subscription_invoice(
                subscription, resource, 'fresh_subscription'
            )
            
            # Mark first payment as completed
            self._set_first_payment_flag(subscription['id'], True)
            
            logger.info(f"Created invoice {invoice_id} for fresh subscription payment {payment_id}")
            
            return {
                'success': True,
                'subscription_id': subscription['id'],
                'invoice_id': invoice_id,
                'amount': amount,
                'payment_type': 'fresh_subscription'
            }
            
        except Exception as e:
            logger.error(f"Error handling fresh subscription payment: {str(e)}")
            raise

    def _handle_renewal_payment(self, subscription, resource):
        """Handle subscription renewal payment"""
        try:
            payment_id = resource.get('id')
            amount = float(resource.get('amount', {}).get('total', 0))
            
            # Create invoice for renewal
            invoice_id = self._create_subscription_invoice(
                subscription, resource, 'renewal'
            )
            
            # Reset resource quota for new billing period
            self.initialize_resource_quota(
                subscription['user_id'], 
                subscription['id'], 
                subscription['app_id']
            )
            
            # Update subscription billing period
            self._update_subscription_billing_period(subscription)
            
            logger.info(f"Processed renewal payment {payment_id} for subscription {subscription['id']}")
            
            return {
                'success': True,
                'subscription_id': subscription['id'],
                'invoice_id': invoice_id,
                'amount': amount,
                'payment_type': 'renewal'
            }
            
        except Exception as e:
            logger.error(f"Error handling renewal payment: {str(e)}")
            raise

    def _handle_upgrade_completion_payment(self, subscription, resource):
        """Handle upgrade completion payment"""
        try:
            payment_id = resource.get('id')
            
            # Get pending upgrade details
            metadata = subscription.get('metadata', {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}
            
            pending_upgrade = metadata.get('pending_paypal_upgrade', {})
            new_plan_id = pending_upgrade.get('new_plan_id')
            
            if not new_plan_id:
                logger.error(f"No pending upgrade found for subscription {subscription['id']}")
                return {'status': 'error', 'message': 'No pending upgrade found'}
            
            # Create invoice for upgrade payment
            invoice_id = self._create_subscription_invoice(
                subscription, resource, 'upgrade'
            )
            
            # Update subscription plan
            self._update_subscription_plan(subscription['id'], new_plan_id)
            
            # Initialize full quota for new plan
            self.initialize_resource_quota(
                subscription['user_id'], 
                subscription['id'], 
                subscription['app_id']
            )
            
            # Clear pending upgrade
            self._clear_pending_upgrade(subscription['id'])
            
            logger.info(f"Completed upgrade payment {payment_id} for subscription {subscription['id']}")
            
            return {
                'success': True,
                'subscription_id': subscription['id'],
                'invoice_id': invoice_id,
                'new_plan_id': new_plan_id,
                'payment_type': 'upgrade'
            }
            
        except Exception as e:
            logger.error(f"Error handling upgrade completion payment: {str(e)}")
            raise

    def _handle_one_time_payment(self, resource):
        """Handle one-time payment (addon, proration, etc.)"""
        try:
            payment_id = resource.get('id')
            logger.info(f"Processed one-time payment: {payment_id}")
            
            return {
                'success': True,
                'payment_id': payment_id,
                'payment_type': 'one_time'
            }
            
        except Exception as e:
            logger.error(f"Error handling one-time payment: {str(e)}")
            raise

    def _handle_subscription_payment_failed(self, payload):
        """Handle BILLING.SUBSCRIPTION.PAYMENT.FAILED"""
        try:
            resource = payload.get('resource', {})
            paypal_subscription_id = resource.get('id')
            
            if not paypal_subscription_id:
                logger.error("No subscription ID in payment failed webhook")
                return {'status': 'error', 'message': 'Missing subscription ID'}
            
            subscription = self._get_subscription_by_paypal_id(paypal_subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found for PayPal ID: {paypal_subscription_id}")
                return {'status': 'error', 'message': 'Subscription not found'}
            
            # Update subscription status
            self._update_subscription_status_by_paypal_id(
                paypal_subscription_id, 
                'payment_failed', 
                resource
            )
            
            # Log the failure
            self.db.log_subscription_action(
                subscription['id'],
                'payment_failed',
                {'paypal_subscription_id': paypal_subscription_id, 'event_data': resource},
                'paypal_webhook'
            )
            
            logger.info(f"PayPal subscription payment failed: {paypal_subscription_id}")
            return {'status': 'success', 'message': 'Payment failure processed'}
            
        except Exception as e:
            logger.error(f"Error handling subscription payment failed: {str(e)}")
            logger.error(traceback.format_exc())
            return {'status': 'error', 'message': str(e)}

    # Additional webhook handlers for cancelled/suspended
    def _handle_subscription_cancelled(self, payload):
        """Handle BILLING.SUBSCRIPTION.CANCELLED"""
        try:
            resource = payload.get('resource', {})
            paypal_subscription_id = resource.get('id')
            
            subscription = self._get_subscription_by_paypal_id(paypal_subscription_id)
            if subscription:
                self._update_subscription_status_by_paypal_id(
                    paypal_subscription_id, 'cancelled', resource
                )
                
                self.db.log_subscription_action(
                    subscription['id'],
                    'cancelled',
                    {'paypal_subscription_id': paypal_subscription_id, 'event_data': resource},
                    'paypal_webhook'
                )
            
            return {'status': 'success', 'message': 'Cancellation processed'}
            
        except Exception as e:
            logger.error(f"Error handling subscription cancelled: {str(e)}")
            return {'status': 'error', 'message': str(e)}

    def _handle_subscription_suspended(self, payload):
        """Handle BILLING.SUBSCRIPTION.SUSPENDED"""
        try:
            resource = payload.get('resource', {})
            paypal_subscription_id = resource.get('id')
            
            subscription = self._get_subscription_by_paypal_id(paypal_subscription_id)
            if subscription:
                self._update_subscription_status_by_paypal_id(
                    paypal_subscription_id, 'suspended', resource
                )
                
                self.db.log_subscription_action(
                    subscription['id'],
                    'suspended',
                    {'paypal_subscription_id': paypal_subscription_id, 'event_data': resource},
                    'paypal_webhook'
                )
            
            return {'status': 'success', 'message': 'Suspension processed'}
            
        except Exception as e:
            logger.error(f"Error handling subscription suspended: {str(e)}")
            return {'status': 'error', 'message': str(e)}

    # =============================================================================
    # UPGRADE METHODS (Moved from service.py)
    # =============================================================================

    def handle_upgrade(self, user_id, subscription_id, new_plan_id, app_id, billing_cycle_info, resource_info):
        """Handle PayPal subscription upgrade"""
        logger.info(f"[PAYPAL UPGRADE] Started: user={user_id}, sub={subscription_id}, plan={new_plan_id}")
        
        try:
            # Get subscription and plans
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

            # Route to appropriate upgrade handler
            current_is_monthly = self._is_monthly_plan(current_plan)
            new_is_annual = self._is_annual_plan(new_plan)
            
            if current_is_monthly:
                # Monthly to any higher plan - immediate update with temp resources
                return self._handle_simple_upgrade(subscription, new_plan, app_id)
                
            else:  # current is annual
                if not new_is_annual:
                    raise ValueError("Cannot downgrade from annual to monthly plan")
                    
                # Annual to Annual - proration payment then update
                return self._handle_annual_upgrade(
                    subscription, current_plan, new_plan, app_id, 
                    billing_cycle_info, resource_info
                )

        except Exception as e:
            logger.error(f"[PAYPAL UPGRADE] Error: {str(e)}")
            raise

    def _handle_simple_upgrade(self, subscription, new_plan, app_id):
        """Handle simple PayPal upgrade (monthly to any)"""
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
            logger.error(f"Error in simple PayPal upgrade: {str(e)}")
            raise

    def _handle_annual_upgrade(self, subscription, current_plan, new_plan, app_id, billing_cycle_info, resource_info):
        """Handle annual to annual PayPal upgrade with proration payment"""
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
            proration_payment_result = self._create_one_time_payment(
                remaining_period_value,
                subscription,
                'Upgrade proration payment'
            )
            
            if proration_payment_result.get('error'):
                raise ValueError(f"Failed to create proration payment: {proration_payment_result['message']}")
            
            # Store pending upgrade details
            self._store_pending_upgrade(
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
            logger.error(f"Error in annual PayPal upgrade: {str(e)}")
            raise

    # =============================================================================
    # CANCELLATION METHODS (Moved from service.py)
    # =============================================================================

    def cancel_subscription(self, user_id, subscription_id):
        """Cancel PayPal subscription immediately but keep access until period end"""
        try:
            # Get subscription data
            subscription = self._get_subscription_for_cancellation(user_id, subscription_id)
           
            paypal_subscription_id = subscription['paypal_subscription_id']
            
            # Cancel with PayPal using our provider
            result = self.paypal.cancel_subscription(paypal_subscription_id)
            
            if result.get('error'):
                logger.error(f"PayPal cancellation failed: {result['message']}")
                raise ValueError(f"PayPal cancellation failed: {result['message']}")
            
            logger.info(f"PayPal subscription cancelled: {paypal_subscription_id}")
            
            # Mark as cancelled in database but keep active until period end
            cancellation_result = self._mark_subscription_cancelled(subscription['id'], subscription)
            
            # Log cancellation
            self.db.log_subscription_action(
                subscription_id,
                'cancellation_requested',
                {
                    'gateway': 'paypal',
                    'cancelled_by': f'user_{user_id}',
                    'cancellation_details': cancellation_result
                },
                f'user_{user_id}'
            )
            
            return cancellation_result
            
        except Exception as e:
            logger.error(f"Error cancelling PayPal subscription: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def _mark_subscription_cancelled(self, subscription_id, subscription):
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



    # =============================================================================
    # HELPER METHODS
    # =============================================================================

    def _extract_webhook_ids(self, payload, provider):
        """Extract entity ID and user ID from webhook payload"""
        entity_id = None
        user_id = None
        
        if provider == 'paypal':
            resource = payload.get('resource', {})
            entity_id = resource.get('id')
            
            # Try to extract user_id from custom_id or billing_agreement_id
            custom_id = resource.get('custom_id', '')
            if custom_id:
                parts = custom_id.split('_')
                if len(parts) >= 2:
                    user_id = parts[1]
            
            # If no user_id from custom_id, try to get from subscription
            if not user_id:
                billing_agreement_id = resource.get('billing_agreement_id')
                if billing_agreement_id:
                    subscription = self._get_subscription_by_paypal_id(billing_agreement_id)
                    if subscription:
                        user_id = subscription.get('user_id')
        
        return entity_id, user_id

    def _get_subscription_by_paypal_id(self, paypal_subscription_id):
        """Get subscription by PayPal subscription ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                SELECT * FROM {DB_TABLE_USER_SUBSCRIPTIONS}
                WHERE paypal_subscription_id = %s
                ORDER BY updated_at DESC LIMIT 1
            """, (paypal_subscription_id,))
            
            subscription = cursor.fetchone()
            cursor.close()
            conn.close()
            
            return subscription
            
        except Exception as e:
            logger.error(f"Error getting subscription by PayPal ID: {str(e)}")
            return None

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

    def _update_subscription_status_by_id(self, subscription_id, status):
        """Update subscription status by ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = %s, updated_at = NOW()
                WHERE id = %s
            """, (status, subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating subscription status: {str(e)}")

    def _update_subscription_status_by_paypal_id(self, paypal_subscription_id, status, data):
        """Update subscription status by PayPal ID"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET status = %s, 
                    updated_at = NOW(),
                    metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s)
                WHERE paypal_subscription_id = %s
            """, (status, json.dumps(data), paypal_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating subscription status by PayPal ID: {str(e)}")
            raise

    def _update_subscription_billing_period(self, subscription):
        """Update subscription billing period for renewal"""
        try:
            # Get plan details for proper interval calculation
            plan = self._get_plan(subscription['plan_id'])
            if not plan:
                logger.error(f"Plan not found for subscription {subscription['id']}")
                return
            
            interval = plan.get('interval', 'month')
            interval_count = plan.get('interval_count', 1)
            
            # Calculate proper interval for SQL
            if interval == 'month':
                sql_interval = f"INTERVAL {interval_count} MONTH"
            elif interval == 'year':
                sql_interval = f"INTERVAL {interval_count} YEAR"
            else:
                # Fallback to monthly
                sql_interval = "INTERVAL 1 MONTH"
                logger.warning(f"Unknown interval '{interval}' for subscription {subscription['id']}, defaulting to monthly")
            
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET current_period_start = NOW(),
                    current_period_end = DATE_ADD(NOW(), {sql_interval}),
                    status = 'active'
                WHERE id = %s
            """, (subscription['id'],))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Updated billing period for renewal with {sql_interval}")
            
        except Exception as e:
            logger.error(f"Error updating subscription billing period: {str(e)}")

    def _set_first_payment_flag(self, subscription_id, completed):
        """Set first payment completed flag in metadata"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                UPDATE {DB_TABLE_USER_SUBSCRIPTIONS}
                SET metadata = JSON_MERGE_PATCH(IFNULL(metadata, '{{}}'), %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (json.dumps({
                'first_payment_completed': completed,
                'first_payment_date': datetime.now().isoformat() if completed else None
            }), subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error setting first payment flag: {str(e)}")

    def _create_subscription_invoice(self, subscription, resource, payment_type):
        """Create invoice for subscription payment"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            invoice_id = generate_id('inv_')
            payment_id = resource.get('id')
            amount = float(resource.get('amount', {}).get('total', 0))
            currency = resource.get('amount', {}).get('currency', 'USD')
            
            cursor.execute("""
                INSERT INTO subscription_invoices
                (id, subscription_id, user_id, paypal_payment_id, amount, currency,
                status, payment_method, invoice_date, paid_at, app_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s)
            """, (
                invoice_id,
                subscription['id'],
                subscription['user_id'],
                payment_id,
                amount,
                currency,
                'paid',
                'paypal',
                subscription['app_id']
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Created invoice {invoice_id} for {payment_type} payment {payment_id}")
            return invoice_id
            
        except Exception as e:
            logger.error(f"Error creating subscription invoice: {str(e)}")
            raise

    def _calculate_subscription_period_from_resource(self, resource, plan_id):
        """Calculate subscription period dates from PayPal resource"""
        start_date = datetime.now()
        
        # Try to get start date from resource
        start_time = resource.get('start_time')
        if start_time:
            try:
                start_date = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                logger.error(f"Invalid start_time value: {start_time}")
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

    def _activate_subscription_with_period(self, paypal_subscription_id, start_date, period_end, resource):
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
                WHERE paypal_subscription_id = %s
            """, (start_date, period_end, json.dumps(resource), paypal_subscription_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error activating subscription with period: {str(e)}")
            raise

    def _create_one_time_payment(self, amount, subscription, description):
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

    def _store_pending_upgrade(self, subscription_id, new_plan_id, order_id):
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
                'pending_paypal_upgrade': {
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

    def handle_proration_completion(self, order_id):
        """Handle completion of PayPal proration payment"""
        try:
            capture_result = self.paypal.capture_order_payment(order_id)
            
            if capture_result.get('error'):
                return {'error': True, 'message': 'Failed to capture payment'}
            
            subscription = self._find_subscription_by_proration_payment(order_id)
            if not subscription:
                return {'error': True, 'message': 'Subscription not found'}
            
            pending_upgrade = subscription.get('metadata', {})
            if isinstance(pending_upgrade, str):
                try:
                    pending_upgrade = json.loads(pending_upgrade)
                except:
                    pending_upgrade = {}
                    
            upgrade_details = pending_upgrade.get('pending_paypal_upgrade', {})
            new_plan_id = upgrade_details.get('new_plan_id')
            
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
                WHERE JSON_EXTRACT(metadata, '$.pending_paypal_upgrade.order_id') = %s
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
               SET metadata = JSON_REMOVE(IFNULL(metadata, '{{}}'), '$.pending_paypal_upgrade'),
                   updated_at = NOW()
               WHERE id = %s
           """, (subscription_id,))
           
           conn.commit()
           cursor.close()
           conn.close()
           
       except Exception as e:
           logger.error(f"Error clearing pending upgrade: {str(e)}")


# Create PayPal service instance
paypal_service = PayPalService()