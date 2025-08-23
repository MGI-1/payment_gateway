"""
Flask routes for payment gateway integration
"""
from .service import payment_service
from .paypal_service import paypal_service
from .utils.helpers import calculate_billing_cycle_info, calculate_resource_utilization
from flask import Blueprint, request, jsonify, current_app,redirect
import json
import logging
import traceback
from .config import get_frontend_url
from .webhooks.razorpay_handler import handle_razorpay_webhook, verify_razorpay_signature
from .webhooks.paypal_handler import handle_paypal_webhook

logger = logging.getLogger('payment_gateway')

def init_payment_routes(app, payment_service, paypal_service=None):
    """
    Initialize payment routes with a Flask app
    
    Args:
        app: Flask application
        payment_service: PaymentService instance
        paypal_service: PayPalService instance (optional for backward compatibility)
    """
    # Create a Blueprint for subscription-related routes
    payment_bp = Blueprint('payment_gateway', __name__, url_prefix='/api/subscriptions')
    
    # Ensure PayPal service is available
    if paypal_service is None:
        paypal_service = getattr(payment_service, 'paypal_service', None)
        if paypal_service is None:
            from .paypal_service import PayPalService
            paypal_service = PayPalService(app)
    
    @payment_bp.route('/plans', methods=['GET'])
    def get_plans():
        """Get all available subscription plans for an app"""
        try:
            app_id = request.args.get('app_id', 'marketfit')
            plans = payment_service.get_available_plans(app_id)
            return jsonify({'plans': plans})
        except Exception as e:
            logger.error(f"Error getting plans: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/user/<user_id>', methods=['GET'])
    def get_user_subscription(user_id):
        """Get a user's active subscription"""
        try:
            app_id = request.args.get('app_id', 'marketfit')
            subscription = payment_service.get_user_subscription(user_id, app_id)
            return jsonify({'subscription': subscription})
        except Exception as e:
            logger.error(f"Error getting user subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/create', methods=['POST'])
    def create_subscription():
        """Create a new subscription for a user"""
        try:
            data = request.json
            user_id = data.get('user_id')
            plan_id = data.get('plan_id')
            app_id = data.get('app_id', 'marketfit')
            
            if not user_id or not plan_id:
                return jsonify({'error': 'User ID and Plan ID are required'}), 400
                
            subscription = payment_service.create_subscription(user_id, plan_id, app_id, preferred_gateway='razorpay')
            return jsonify({'subscription': subscription})
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/cancel/<subscription_id>', methods=['POST'])
    def cancel_subscription(subscription_id):
        """Cancel subscription with gateway detection"""
        try:
            data = request.json
            user_id = data.get('user_id')
            
            if not user_id:
                return jsonify({'error': 'User ID is required'}), 400
            
            # Get subscription to determine gateway
            subscription = payment_service._get_subscription_details(subscription_id)
            
            if subscription.get('paypal_subscription_id'):
                # Use PayPal service for PayPal subscriptions
                result = paypal_service.cancel_subscription(user_id, subscription_id)
            elif subscription.get('razorpay_subscription_id'):
                # Use main payment service for Razorpay
                result = payment_service.cancel_subscription(user_id, subscription_id)
            else:
                return jsonify({'error': 'No gateway subscription found'}), 400
            
            return jsonify({'result': result})
            
        except Exception as e:
            logger.error(f"Error cancelling subscription: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/razorpay-webhook', methods=['POST'])
    def razorpay_webhook():
        """Handle Razorpay webhook events"""
        logger.info("Received Razorpay webhook")
        result, status_code = handle_razorpay_webhook(payment_service)
        return jsonify(result), status_code

    @payment_bp.route('/paypal-webhook', methods=['POST'])
    def paypal_webhook():
        """Handle PayPal webhook events using PayPal service"""
        logger.info("Received PayPal webhook")
        result, status_code = handle_paypal_webhook()  # Uses paypal_service internally
        return jsonify(result), status_code


    @payment_bp.route('/verify-payment', methods=['POST'])
    def verify_payment():
        """Manually verify a Razorpay payment"""
        try:
            data = request.json
            payment_id = data.get('razorpay_payment_id')
            subscription_id = data.get('razorpay_subscription_id')
            signature = data.get('razorpay_signature')
            user_id = data.get('user_id')
            
            if not payment_id or not subscription_id or not signature or not user_id:
                return jsonify({'error': 'Missing required parameters'}), 400
            
            # Verify the payment signature
            payload = f"{payment_id}|{subscription_id}"
            if not verify_razorpay_signature(payload.encode(), signature):
                return jsonify({'error': 'Invalid signature'}), 400
            
            # If signature is valid, manually activate the subscription
            result = payment_service.activate_subscription(
                user_id, 
                subscription_id, 
                payment_id
            )
            
            return jsonify({'result': result})
        except Exception as e:
            logger.error(f"Error verifying payment: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/billing-history', methods=['GET'])
    def get_billing_history():
        """Get billing history for a user"""
        try:
            user_id = request.args.get('user_id')
            app_id = request.args.get('app_id', 'marketfit')
            
            if not user_id:
                return jsonify({'error': 'User ID is required'}), 400
                
            invoices = payment_service.get_billing_history(user_id, app_id)
            return jsonify({'invoices': invoices})
        except Exception as e:
            logger.error(f"Error getting billing history: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500
        
    # routes.py - Add new endpoint for checking and decrementing resource quota

    @payment_bp.route('/check-resource', methods=['POST'])
    def check_resource():
        """Check if a user has enough resources for an action"""
        try:
            data = request.json
            user_id = data.get('user_id')
            app_id = data.get('app_id', 'marketfit')
            resource_type = data.get('resource_type')
            count = data.get('count', 1)
            
            
            if not all([user_id, resource_type]):
                logger.warning("[AZURE DEBUG] Missing required parameters")
                return jsonify({'error': 'User ID and resource type are required'}), 400
                
            result = payment_service.check_resource_availability(
                user_id, app_id, resource_type, count
            )
            logger.debug(f"[AZURE DEBUG] check_resource_availability result: {result}")
            
            if result:
                return jsonify({'available': True})
            else:
                return jsonify({
                    'available': False,
                    'message': 'You have reached your resource limit for this billing period.'
                })
                
        except Exception as e:
            logger.error(f"[AZURE DEBUG] Error in check-resource endpoint: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/decrement-resource', methods=['POST'])
    def decrement_resource():
        """Decrement resource quota for a user"""
        try:
            data = request.json
            user_id = data.get('user_id')
            app_id = data.get('app_id', 'marketfit')
            resource_type = data.get('resource_type')
            count = data.get('count', 1)
                        
            if not all([user_id, resource_type]):
                logger.warning("[AZURE DEBUG] Missing required parameters")
                return jsonify({'error': 'User ID and resource type are required'}), 400
                
            result = payment_service.decrement_resource_quota(
                user_id, app_id, resource_type, count
            )
            logger.debug(f"[AZURE DEBUG] decrement_resource_quota result: {result}")
            
            if result:
                return jsonify({'success': True})
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to decrement resource quota. You may have reached your limit.'
                })
                
        except Exception as e:
            logger.error(f"[AZURE DEBUG] Error in decrement-resource endpoint: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/resource-quota', methods=['GET'])
    def get_resource_quota():
        """Get resource quota for a user"""
        try:
            user_id = request.args.get('user_id')
            app_id = request.args.get('app_id', 'marketfit')
            
            logger.info(f"[AZURE DEBUG] resource-quota params: user_id={user_id}, app_id={app_id}")
            
            if not user_id:
                logger.warning("[AZURE DEBUG] Missing user_id parameter")
                return jsonify({'error': 'User ID is required'}), 400
                
            quota = payment_service.get_resource_quota(user_id, app_id)
            
            return jsonify({'quota': quota})
            
        except Exception as e:
            logger.error(f"[AZURE DEBUG] Error in resource-quota endpoint: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/initialize-quota', methods=['POST'])
    def initialize_quota():
        """Initialize or reset resource quota for a user"""
        try:
            data = request.json
            user_id = data.get('user_id')
            app_id = data.get('app_id', 'marketfit')
            
            if not user_id:
                return jsonify({'error': 'User ID is required'}), 400
            
            # Get the user's active subscription
            subscription = payment_service.get_user_subscription(user_id, app_id)
            
            if not subscription:
                return jsonify({'error': 'No active subscription found'}), 404
            
            result = payment_service.initialize_resource_quota(
                user_id, subscription['id'], app_id
            )
            
            if result:
                return jsonify({'success': True})
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to initialize resource quota.'
                })
                
        except Exception as e:
            logger.error(f"Error initializing resource quota: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500
       
    @payment_bp.route('/ensure-resource-quota', methods=['POST'])
    def ensure_resource_quota():
        """Ensure user has a resource quota entry"""
        try:
            data = request.json
            user_id = data.get('user_id')
            app_id = data.get('app_id', 'marketfit')
            
            
            if not user_id:
                logger.warning("[AZURE DEBUG] Missing user_id parameter")
                return jsonify({'error': 'User ID is required'}), 400
                
            result = payment_service.ensure_user_has_resource_quota(user_id, app_id)
            
            return jsonify({'success': result})
                
        except Exception as e:
            logger.error(f"[AZURE DEBUG] Error in ensure-resource-quota endpoint: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/create-paypal', methods=['POST'])
    def create_paypal_subscription():
        """Create PayPal subscription using PayPal service"""
        try:
            data = request.json
            user_id = data.get('user_id')
            plan_id = data.get('plan_id')
            app_id = data.get('app_id', 'marketfit')
            customer_info = data.get('customer_info')
            
            if not all([user_id, plan_id]):
                return jsonify({'error': 'User ID and plan ID are required'}), 400
            
            # Use PayPal service instead of main payment service
            result = paypal_service.create_subscription(
                user_id, plan_id, app_id, customer_info
            )
            
            return jsonify({'result': result})
            
        except Exception as e:
            logger.error(f"Error creating PayPal subscription: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/paypal-success', methods=['GET'])
    def paypal_subscription_success():
        """Handle PayPal subscription approval success with smart routing"""
        try:
            subscription_id = request.args.get('subscription_id')
            
            if not subscription_id:
                logger.warning("PayPal success handler called without subscription_id")
                return redirect(f"{get_frontend_url()}/subscription-dashboard?error=Missing%20subscription%20information")
            
            logger.info(f"Processing PayPal success for subscription: {subscription_id}")
            
            # Use PayPal ID lookup directly since PayPal returns PayPal subscription ID
            subscription = paypal_service._get_subscription_by_paypal_id(subscription_id)
            
            if not subscription:
                logger.error(f"Subscription not found by PayPal ID: {subscription_id}")
                return redirect(f"{get_frontend_url()}/subscription-dashboard?error=Subscription%20not%20found")
            
            logger.info(f"Found subscription: {subscription['id']} for PayPal ID: {subscription_id}")
            
            # Parse metadata safely
            metadata = subscription.get('metadata', {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid metadata JSON for subscription {subscription['id']}")
                    metadata = {}
            
            # Handle upgrade approval completion
            if metadata.get('upgrade_pending_approval'):
                logger.info(f"Processing upgrade approval completion for subscription {subscription['id']}")
                
                result = paypal_service.complete_simple_upgrade_after_approval(subscription['id'])
                
                if result.get('error'):
                    error_msg = result.get('message', 'Unknown error occurred during upgrade completion')
                    logger.error(f"Upgrade completion failed for {subscription['id']}: {error_msg}")
                    return redirect(f"{get_frontend_url()}/subscription-dashboard?error={error_msg}")
                
                logger.info(f"Upgrade approval completed successfully for subscription {subscription['id']}")
                success_message = "Upgrade%20completed%20successfully!%20Your%20new%20plan%20is%20now%20active%20with%20temporary%20resources%20until%20next%20billing%20cycle."
                return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=success&message={success_message}")
            
            # Handle regular subscription success cases
            else:
                logger.info(f"Handling regular subscription return for {subscription['id']}, current status: {subscription.get('status')}")
                
                if subscription['status'] == 'active':
                    logger.info(f"Subscription {subscription['id']} already active via webhook")
                    return redirect(f"{get_frontend_url()}/subscription-dashboard?status=active&message=Your%20subscription%20is%20active%20and%20ready%20to%20use!")
                else:
                    logger.info(f"Subscription {subscription['id']} still processing")
                    return redirect(f"{get_frontend_url()}/subscription-dashboard?status=processing&message=Your%20subscription%20is%20being%20processed.%20Please%20check%20back%20in%20a%20few%20minutes.")
            
        except Exception as e:
            logger.error(f"Error in PayPal success handler: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            error_message = "An%20error%20occurred%20while%20processing%20your%20subscription.%20Please%20contact%20support%20if%20this%20persists."
            return redirect(f"{get_frontend_url()}/subscription-dashboard?error={error_message}")

    @payment_bp.route('/paypal-cancel', methods=['GET'])
    def paypal_subscription_cancel():
        """Handle PayPal subscription approval cancellation"""
        try:
            subscription_id = request.args.get('subscription_id')
            
            if subscription_id:
                # ✅ CHANGE: Use PayPal ID lookup like the success handler
                subscription = paypal_service._get_subscription_by_paypal_id(subscription_id)
                if subscription:
                    metadata = subscription.get('metadata', {})
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except:
                            metadata = {}
                    
                    if metadata.get('upgrade_pending_approval'):
                        # Clear pending upgrade metadata
                        paypal_service._clear_upgrade_pending_metadata(subscription['id'])  # Use internal ID
                        logger.info(f"Cleared pending upgrade for cancelled approval: {subscription['id']}")
                        cancel_message = "Upgrade%20cancelled.%20Your%20current%20plan%20remains%20active."
                        return redirect(f"{get_frontend_url()}/subscription-dashboard?cancelled=upgrade&message={cancel_message}")
                    else:
                        # Regular subscription cancellation
                        paypal_service.cancel_pending_subscription(subscription['id'])  # Use internal ID
                        cancel_message = "Subscription%20setup%20cancelled."
                        return redirect(f"{get_frontend_url()}/subscription-dashboard?cancelled=subscription&message={cancel_message}")
            
            # Default cancellation case
            cancel_message = "Payment%20was%20cancelled."
            return redirect(f"{get_frontend_url()}/subscription-dashboard?cancelled=true&message={cancel_message}")
            
        except Exception as e:
            logger.error(f"Error handling PayPal cancel: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            error_message = "Cancellation%20processing%20failed.%20Please%20contact%20support%20if%20needed."
            return redirect(f"{get_frontend_url()}/subscription-dashboard?error={error_message}")
    
    # Add these new routes to your routes.py file

    @payment_bp.route('/paypal-proration-complete', methods=['GET'])
    def paypal_proration_complete():
        """Handle PayPal proration payment user redirects after payment completion"""
        try:
            payment_type = request.args.get('type')
            token = request.args.get('token')
            payer_id = request.args.get('PayerID')
            
            logger.info(f"PayPal proration completion: type={payment_type}, token={token}, payer_id={payer_id}")
            
            if payment_type == 'proration' and token:
                # Process the proration payment completion
                result = paypal_service.handle_proration_completion(token)
                
                if result.get('success'):
                    if result.get('requires_additional_approval'):
                        # ✅ Redirect to PayPal for additional approval
                        approval_url = result.get('approval_url')
                        logger.info(f"Redirecting to PayPal approval: {approval_url}")
                        return redirect(approval_url)
                    else:
                        # ✅ Upgrade completed successfully
                        logger.info(f"Proration payment completed successfully: {token}")
                        return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=success&message=Upgrade completed successfully!")
                else:
                    error_msg = result.get('message', 'Unknown error occurred')
                    logger.error(f"Proration payment failed: {token}, error: {error_msg}")
                    return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=error&message={error_msg}")
            
            elif payment_type == 'proration_cancel':
                logger.info(f"Proration payment cancelled: {token}")
                return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=cancelled&message=Upgrade payment was cancelled. Your current plan remains active.")
            
            else:
                # Generic PayPal payment completion
                logger.info(f"Generic PayPal payment completion: type={payment_type}")
                return redirect(f"{get_frontend_url()}/subscription-dashboard?payment=success&message=PayPal payment completed successfully!")
            
        except Exception as e:
            logger.error(f"Error in PayPal proration completion: {str(e)}")
            logger.error(traceback.format_exc())
            return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=error&message=Payment processing failed. Please contact support if payment was deducted.")

    @payment_bp.route('/paypal-approval-complete', methods=['GET'])
    def paypal_approval_complete():
        """Handle completion of PayPal subscription approval"""
        try:
            subscription_id = request.args.get('subscription_id')  # This is PayPal subscription ID
            token = request.args.get('token')
            
            logger.info(f"PayPal approval completion: subscription_id={subscription_id}, token={token}")
            
            if subscription_id:
                # ✅ FIX: Find subscription by PayPal ID, not internal ID
                subscription = paypal_service._get_subscription_by_paypal_id(subscription_id)
                
                if not subscription:
                    logger.error(f"Subscription not found by PayPal ID: {subscription_id}")
                    return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=error&message=Subscription not found")
                
                # Complete the upgrade using internal subscription ID
                result = paypal_service.complete_approved_upgrade(subscription['id'])  # Use internal ID
                
                if result.get('success'):
                    logger.info(f"Approval completed successfully for subscription {subscription['id']}")
                    return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=success&message=Upgrade completed successfully! Your subscription has been updated.")
                else:
                    error_msg = result.get('message', 'Unknown error occurred')
                    logger.error(f"Approval completion failed for {subscription['id']}: {error_msg}")
                    return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=error&message={error_msg}")
            
            logger.warning("PayPal approval completion called without subscription_id")
            return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=error&message=Invalid approval completion")
            
        except Exception as e:
            logger.error(f"Error in PayPal approval completion: {str(e)}")
            logger.error(traceback.format_exc())
            return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=error&message=Approval processing failed. Please contact support.")

    @payment_bp.route('/paypal-approval-cancel', methods=['GET'])
    def paypal_approval_cancel():
        """Handle PayPal subscription approval cancellation"""
        try:
            subscription_id = request.args.get('subscription_id')
            
            logger.info(f"PayPal approval cancelled: subscription_id={subscription_id}")
            
            if subscription_id:
                # Clear the approval metadata since user cancelled
                try:
                    paypal_service._clear_approval_metadata(subscription_id)
                    logger.info(f"Cleared approval metadata for cancelled approval: {subscription_id}")
                except Exception as e:
                    logger.error(f"Error clearing approval metadata: {str(e)}")
            
            return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=cancelled&message=Subscription authorization was cancelled. Your current plan remains active.")
            
        except Exception as e:
            logger.error(f"Error handling PayPal approval cancel: {str(e)}")
            return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=error&message=Cancellation processing failed.")

    @payment_bp.route('/razorpay-payment-complete', methods=['GET'])
    def razorpay_payment_complete():
        """Handle Razorpay payment link user redirects after payment completion"""
        try:
            # Extract payment details from query parameters
            payment_id = request.args.get('razorpay_payment_id')
            order_id = request.args.get('razorpay_order_id') 
            signature = request.args.get('razorpay_signature')
            status = request.args.get('status', 'unknown')
            
            logger.info(f"Razorpay payment completion redirect: payment_id={payment_id}, status={status}")
            
            # Log the completion for audit trail
            if payment_id:
                payment_service.db.log_event(
                    'razorpay_payment_link_completed',
                    payment_id,
                    None,  # user_id not available in redirect
                    {
                        'payment_id': payment_id,
                        'order_id': order_id,
                        'status': status,
                        'signature_present': bool(signature)
                    },
                    provider='razorpay',
                    processed=True
                )
            
            # Determine redirect message based on status
            if status == 'success' or payment_id:
                return redirect(f"{get_frontend_url()}/subscription-dashboard?payment=success&message=Payment completed successfully!'")
            elif status == 'failed':
                return redirect(f"{get_frontend_url()}/subscription-dashboard?payment=failed&message=Payment was not successful. Please try again.'")
            else:
                return redirect(f"{get_frontend_url()}/subscription-dashboard?payment=completed&message=Payment processing completed.'")
                
        except Exception as e:
            logger.error(f"Error in Razorpay payment completion: {str(e)}")
            logger.error(traceback.format_exc())
            return redirect(f"{get_frontend_url()}/subscription-dashboard?error=Payment completion processing failed. Please contact support if payment was deducted.'")

    @payment_bp.route('/paypal-proration-cancel', methods=['GET'])
    def paypal_proration_cancel():
        """Handle PayPal proration payment cancellation redirects"""
        try:
            payment_type = request.args.get('type')
            token = request.args.get('token')
            
            logger.info(f"PayPal proration cancellation: type={payment_type}, token={token}")
            
            if payment_type == 'proration':
                return redirect(f"{get_frontend_url()}/subscription-dashboard?upgrade=cancelled&message=Upgrade payment was cancelled. Your current plan remains active.'")
            else:
                return redirect(f"{get_frontend_url()}/subscription-dashboard?payment=cancelled&message=Payment was cancelled.'")
            
        except Exception as e:
            logger.error(f"Error in PayPal proration cancellation: {str(e)}")
            return redirect(f"{get_frontend_url()}/subscription-dashboard?payment=error&message=Cancellation processing failed.'")
                    
    @payment_bp.route('/upgrade', methods=['POST'])
    def upgrade_subscription():
        """Handle upgrade with gateway parameter from frontend"""
        try:
            logger.info("[UPGRADE] Route started")
            data = request.json
            user_id = data.get('user_id')
            subscription_id = data.get('subscription_id')
            new_plan_id = data.get('new_plan_id')
            app_id = data.get('app_id', 'marketfit')
            current_gateway = data.get('current_gateway')
            
            logger.info(f"[UPGRADE] Params: user={user_id}, sub={subscription_id}, plan={new_plan_id}, gateway={current_gateway}")
            
            if not all([user_id, subscription_id, new_plan_id, current_gateway]):
                logger.info("[UPGRADE] Missing required parameters")
                return jsonify({'error': 'User ID, subscription ID, new plan ID, and current gateway are required'}), 400
            
            # ✅ ONLY CHANGE: Convert to internal ID
            plan_record = payment_service._get_plan(new_plan_id)
            if not plan_record:
                return jsonify({'error': 'Plan not found'}), 400
            internal_plan_id = plan_record['id']
            
            # Validate gateway parameter
            if current_gateway not in ['paypal', 'razorpay']:
                return jsonify({'error': 'Invalid gateway. Must be paypal or razorpay'}), 400
            
            # Get subscription to validate gateway matches
            subscription = payment_service._get_subscription_details(subscription_id)
            if not subscription or subscription['user_id'] != user_id:
                return jsonify({'error': 'Subscription not found or access denied'}), 400
            
            # Validate gateway matches subscription
            if current_gateway == 'paypal' and not subscription.get('paypal_subscription_id'):
                return jsonify({'error': 'Gateway mismatch - not a PayPal subscription'}), 400
            
            if current_gateway == 'razorpay' and not subscription.get('razorpay_subscription_id'):
                return jsonify({'error': 'Gateway mismatch - not a Razorpay subscription'}), 400
            
            logger.info(f"[UPGRADE] Gateway validation passed: {current_gateway}")
            
            # Route to appropriate service based on current gateway
            if current_gateway == 'paypal':
                # Get usage data for PayPal upgrade
                usage_data = paypal_service.get_current_usage(user_id, subscription_id, app_id)
                if not usage_data:
                    raise ValueError("Usage data not found")

                # âœ… DEBUG: Log the actual usage data
                logger.info("=== BILLING DEBUG ===")
                logger.info(f"usage_data type: {type(usage_data)}")
                logger.info(f"usage_data keys: {list(usage_data.keys()) if isinstance(usage_data, dict) else 'Not a dict'}")
                logger.info(f"billing_period_start: {usage_data.get('billing_period_start')} (type: {type(usage_data.get('billing_period_start'))})")
                logger.info(f"billing_period_end: {usage_data.get('billing_period_end')} (type: {type(usage_data.get('billing_period_end'))})")
                logger.info(f"Full usage_data: {usage_data}")
                logger.info("====================")

                # âœ… Method 1: Check using usage_data billing period
                billing_period_end = usage_data.get('billing_period_end')
                logger.info(f"[DEBUG] Raw billing_period_end: {billing_period_end}")

                if billing_period_end:
                    from datetime import datetime, timedelta
                    
                    # Handle both datetime objects and strings
                    if isinstance(billing_period_end, str):
                        logger.info(f"[DEBUG] Converting string to datetime: {billing_period_end}")
                        billing_end = datetime.fromisoformat(billing_period_end.replace('Z', '+00:00'))
                    else:
                        logger.info(f"[DEBUG] Using datetime object directly")
                        billing_end = billing_period_end
                    
                    now = datetime.now(billing_end.tzinfo if billing_end.tzinfo else None)
                    two_days_from_now = now + timedelta(days=2)
                    
                    logger.info(f"[DEBUG] billing_end: {billing_end}")
                    logger.info(f"[DEBUG] now: {now}")
                    logger.info(f"[DEBUG] two_days_from_now: {two_days_from_now}")
                    logger.info(f"[DEBUG] billing_end <= two_days_from_now: {billing_end <= two_days_from_now}")
                    
                    if billing_end <= two_days_from_now:
                        logger.info(f"[UPGRADE] Billing within 2 days ({billing_end}), blocking upgrade")
                        return jsonify({
                            'result': {
                                'success': False,
                                'error_type': 'billing_cycle_timing',
                                'message': 'Your next billing cycle is within the next 2 days. Please try upgrading after your billing cycle completes to avoid any issues.',
                                'title': 'Upgrade Temporarily Unavailable',
                                'action_required': 'retry_after_billing'
                            }
                        }), 200
                    else:
                        logger.info(f"[DEBUG] Billing is more than 2 days away, proceeding with upgrade")
                else:
                    logger.warning(f"[DEBUG] No billing_period_end found in usage_data, trying PayPal API")

                # âœ… Method 2: Check PayPal subscription directly if usage_data doesn't have billing info
                paypal_subscription_id = subscription.get('paypal_subscription_id')
                logger.info(f"[DEBUG] PayPal subscription ID: {paypal_subscription_id}")
                
                if paypal_subscription_id and not billing_period_end:
                    # Get PayPal subscription details directly
                    paypal_details = paypal_service.paypal.get_subscription(paypal_subscription_id)
                    logger.info(f"[DEBUG] PayPal subscription details response: {json.dumps(paypal_details, indent=2, default=str)}")
                    
                    if not paypal_details.get('error'):
                        billing_info = paypal_details.get('billing_info', {})
                        next_billing_time = billing_info.get('next_billing_time')
                        logger.info(f"[DEBUG] PayPal next_billing_time: {next_billing_time}")
                        
                        if next_billing_time:
                            from datetime import datetime, timedelta
                            next_billing = datetime.fromisoformat(next_billing_time.replace('Z', '+00:00'))
                            now = datetime.now(next_billing.tzinfo)
                            two_days_from_now = now + timedelta(days=2)
                            
                            logger.info(f"[DEBUG] PayPal next_billing: {next_billing}")
                            logger.info(f"[DEBUG] now: {now}")
                            logger.info(f"[DEBUG] two_days_from_now: {two_days_from_now}")
                            logger.info(f"[DEBUG] next_billing <= two_days_from_now: {next_billing <= two_days_from_now}")
                            
                            if next_billing <= two_days_from_now:
                                logger.info(f"[UPGRADE] PayPal billing within 2 days, blocking upgrade")
                                return jsonify({
                                    'result': {
                                        'success': False,
                                        'error_type': 'billing_cycle_timing',
                                        'message': 'Your next billing cycle is within the next 2 days. Please try upgrading after your billing cycle completes to avoid any issues.',
                                        'title': 'Upgrade Temporarily Unavailable',
                                        'action_required': 'retry_after_billing'
                                    }
                                }), 200
                            else:
                                logger.info(f"[DEBUG] PayPal billing is more than 2 days away, proceeding with upgrade")
                        else:
                            logger.warning(f"[DEBUG] No next_billing_time found in PayPal API response")
                    else:
                        logger.error(f"[DEBUG] Error getting PayPal subscription details: {paypal_details}")

                # If we reach here, billing check passed or couldn't be determined - proceed with upgrade
                logger.info(f"[DEBUG] Billing check completed, proceeding with upgrade")

                # Get current plan for resource calculation
                current_plan = paypal_service._get_plan(subscription['plan_id'])

                billing_cycle_info = calculate_billing_cycle_info(
                    usage_data['billing_period_start'],
                    usage_data['billing_period_end']
                )

                resource_info = calculate_resource_utilization(
                    usage_data,
                    current_plan['features'],
                    app_id
                )
                
                # Use PayPal service for upgrade
                logger.info("[UPGRADE] Calling paypal_service.handle_upgrade")
                result = paypal_service.handle_upgrade(
                    user_id, subscription_id, internal_plan_id, app_id,  # ← Changed to internal_plan_id
                    billing_cycle_info, resource_info
                )
                logger.info("[UPGRADE] PayPal service returned successfully")
                
            elif current_gateway == 'razorpay':
                # Use main payment service for Razorpay
                logger.info("[UPGRADE] Calling payment_service.upgrade_subscription")
                result = payment_service.upgrade_subscription(user_id, subscription_id, internal_plan_id, app_id)  # ← Changed to internal_plan_id
                logger.info("[UPGRADE] Payment service returned successfully")
            
            # âœ… ADD COMPREHENSIVE RESULT LOGGING
            logger.info("=== UPGRADE ROUTE RESPONSE DEBUG ===")
            logger.info(f"Route result type: {type(result)}")
            logger.info(f"Route result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")
            logger.info(f"Route result success: {result.get('success') if isinstance(result, dict) else 'N/A'}")
            logger.info(f"Route result requires_approval: {result.get('requires_approval') if isinstance(result, dict) else 'N/A'}")
            logger.info(f"Route result approval_url: {result.get('approval_url') if isinstance(result, dict) else 'N/A'}")
            logger.info(f"Route result upgrade_type: {result.get('upgrade_type') if isinstance(result, dict) else 'N/A'}")
            logger.info(f"Route result message: {result.get('message') if isinstance(result, dict) else 'N/A'}")
            logger.info(f"Full route result: {json.dumps(result, indent=2, default=str)}")
            logger.info("=====================================")
            
            return jsonify({'result': result})
            
        except Exception as e:
            logger.error(f"[UPGRADE] Route exception: {str(e)}")
            logger.error(f"[UPGRADE] Exception type: {type(e)}")
            logger.error(f"[UPGRADE] Exception args: {e.args}")
            import traceback
            logger.error(f"[UPGRADE] Traceback: {traceback.format_exc()}")
            return jsonify({'error': str(e)}), 500
        
    @payment_bp.route('/downgrade-request', methods=['POST'])
    def request_downgrade():
        """Handle downgrade request - log for manual processing"""
        try:
            data = request.json
            user_id = data.get('user_id')
            subscription_id = data.get('subscription_id')
            new_plan_id = data.get('new_plan_id')
            app_id = data.get('app_id', 'marketfit')
            
            # Log the downgrade request
            payment_service.db.log_subscription_action(
                subscription_id,
                'downgrade_requested',
                {
                    'user_id': user_id,
                    'requested_plan': new_plan_id,
                    'app_id': app_id,
                    'status': 'pending_manual_processing'
                },
                f'user_{user_id}'
            )
            
            return jsonify({
                'success': True,
                'message': 'Downgrade request submitted. Our team will process it by the end of your current billing cycle.',
                'status': 'pending'
            })
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/purchase-addon', methods=['POST'])
    def purchase_addon():
        """Purchase additional resources"""
        try:
            data = request.json
            user_id = data.get('user_id')
            app_id = data.get('app_id', 'marketfit')
            addon_type = data.get('addon_type')  # 'document_pages', 'perplexity_requests', 'requests'
            quantity = data.get('quantity')
            amount_paid = data.get('amount_paid')
            payment_id = data.get('payment_id')  # From Razorpay/PayPal
            
            if not all([user_id, addon_type, quantity, amount_paid]):
                return jsonify({'error': 'Missing required parameters'}), 400
            
            result = payment_service.purchase_addon(
                user_id, app_id, addon_type, quantity, amount_paid, payment_id
            )
            
            return jsonify({'result': result})
            
        except Exception as e:
            logger.error(f"Error purchasing addon: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/subscription/<subscription_id>/usage', methods=['GET'])
    def get_subscription_usage(subscription_id):
        """Get current usage for a subscription"""
        try:
            user_id = request.args.get('user_id')
            app_id = request.args.get('app_id', 'marketfit')
            
            if not user_id:
                return jsonify({'error': 'User ID is required'}), 400
            
            usage = payment_service.get_current_usage(user_id, subscription_id, app_id)
            
            if not usage:
                return jsonify({'error': 'Usage data not found'}), 404
            
            return jsonify({'usage': usage})
            
        except Exception as e:
            logger.error(f"Error getting subscription usage: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/user/<user_id>/addons', methods=['GET'])
    def get_user_addons(user_id):
        """Get user's addon purchase history"""
        try:
            app_id = request.args.get('app_id', 'marketfit')
            
            conn = payment_service.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT addon_type, quantity, consumed_quantity, amount_paid, 
                    purchased_at, billing_period_end, status
                FROM resource_addons
                WHERE user_id = %s AND app_id = %s
                ORDER BY purchased_at DESC
            """, (user_id, app_id))
            
            addons = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            return jsonify({'addons': addons})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/subscription/<subscription_id>/audit-log', methods=['GET'])
    def get_subscription_audit_log(subscription_id):
        """Get audit log for a subscription"""
        try:
            user_id = request.args.get('user_id')
            
            if not user_id:
                return jsonify({'error': 'User ID is required'}), 400
            
            # Verify user owns subscription
            subscription = payment_service._get_subscription_details(subscription_id)
            if not subscription or subscription['user_id'] != user_id:
                return jsonify({'error': 'Subscription not found or access denied'}), 404
            
            # Get audit log
            conn = payment_service.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
            SELECT action_type, details, initiated_by, created_at
            FROM subscription_audit_log
            WHERE subscription_id = %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (subscription_id,))
        
            audit_log = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            return jsonify({'audit_log': audit_log})
        
        except Exception as e:
            logger.error(f"Error getting audit log: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/manual-refunds', methods=['GET'])
    def get_manual_refunds():
        """Get pending manual refunds for admin processing"""
        try:
            status_filter = request.args.get('status', 'scheduled')
            
            conn = payment_service.db.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT mr.*, us.plan_id, sp.name as plan_name
                FROM manual_refunds mr
                LEFT JOIN user_subscriptions us ON mr.subscription_id = us.id
                LEFT JOIN subscription_plans sp ON us.plan_id = sp.id
                WHERE mr.status = %s
                ORDER BY mr.scheduled_at DESC
            """, (status_filter,))
            
            refunds = cursor.fetchall()
            cursor.close()
            conn.close()
            
            return jsonify({'refunds': refunds})
            
        except Exception as e:
            logger.error(f"Error getting manual refunds: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/manual-refunds/<refund_id>/process', methods=['POST'])
    def process_manual_refund(refund_id):
        """Mark manual refund as processed"""
        try:
            data = request.json
            processed_by = data.get('processed_by', 'admin')
            admin_notes = data.get('admin_notes', '')
            new_status = data.get('status', 'completed')
            
            conn = payment_service.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE manual_refunds 
                SET status = %s, processed_by = %s, admin_notes = %s, 
                    processed_at = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (new_status, processed_by, admin_notes, refund_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return jsonify({'success': True, 'message': 'Refund status updated'})
            
        except Exception as e:
            logger.error(f"Error processing manual refund: {str(e)}")
            return jsonify({'error': str(e)}), 500

    # Register the blueprint with the app
    app.register_blueprint(payment_bp)
    
    # Log that routes were initialized
    logger.debug("Payment gateway routes initialized")