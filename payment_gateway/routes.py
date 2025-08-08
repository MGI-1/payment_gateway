"""
Flask routes for payment gateway integration
"""
from flask import Blueprint, request, jsonify, current_app,redirect
import logging
import traceback

from .webhooks.razorpay_handler import handle_razorpay_webhook, verify_razorpay_signature
from .webhooks.paypal_handler import handle_paypal_webhook

logger = logging.getLogger('payment_gateway')

def init_payment_routes(app, payment_service):
    """
    Initialize payment routes with a Flask app
    
    Args:
        app: Flask application
        payment_service: PaymentService instance
    """
    # Create a Blueprint for subscription-related routes
    payment_bp = Blueprint('payment_gateway', __name__, url_prefix='/api/subscriptions')
    
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
            redirect_url = data.get('redirect_url')  # Get the redirect URL from the request
            
            if not user_id or not plan_id:
                return jsonify({'error': 'User ID and Plan ID are required'}), 400
                
            # Pass the redirect URL to the payment service
            subscription = payment_service.create_subscription(
                user_id, 
                plan_id, 
                app_id, 
                preferred_gateway='razorpay',
                redirect_url=redirect_url  # Pass the redirect URL
            )
            
            return jsonify({'subscription': subscription})
        except Exception as e:
            logger.error(f"Error creating subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/cancel/<subscription_id>', methods=['POST'])
    def cancel_subscription(subscription_id):
        """Cancel a subscription"""
        try:
            data = request.json
            user_id = data.get('user_id')
            
            if not user_id:
                return jsonify({'error': 'User ID is required'}), 400
                
            result = payment_service.cancel_subscription(user_id, subscription_id)
            return jsonify({'result': result})
        except Exception as e:
            logger.error(f"Error cancelling subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/razorpay-webhook', methods=['POST'])
    def razorpay_webhook():
        """Handle Razorpay webhook events"""
        logger.info("Received Razorpay webhook")
        result, status_code = handle_razorpay_webhook(payment_service)
        return jsonify(result), status_code

    @payment_bp.route('/paypal-webhook', methods=['POST'])
    def paypal_webhook():
        """Handle PayPal webhook events"""
        logger.info("Received PayPal webhook")
        result, status_code = handle_paypal_webhook(payment_service)
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
    
    @payment_bp.route('/record-paypal', methods=['POST'])
    def record_paypal_subscription():
        """Record a PayPal subscription in the database"""
        try:
            data = request.json
            user_id = data.get('user_id')
            plan_id = data.get('plan_id')
            app_id = data.get('app_id', 'marketfit')
            paypal_subscription_id = data.get('paypal_subscription_id')
            
            if not user_id or not plan_id or not paypal_subscription_id:
                return jsonify({'error': 'Missing required parameters'}), 400
            
            # Create or update subscription record
            subscription = payment_service.create_paypal_subscription(
                user_id, 
                plan_id, 
                paypal_subscription_id,
                app_id
            )
            
            return jsonify({'subscription': subscription})
        except Exception as e:
            logger.error(f"Error recording PayPal subscription: {str(e)}")
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
        """Create PayPal subscription using backend API"""
        try:
            data = request.json
            user_id = data.get('user_id')
            plan_id = data.get('plan_id')
            app_id = data.get('app_id', 'marketfit')
            customer_info = data.get('customer_info')  # Optional
            
            if not all([user_id, plan_id]):
                return jsonify({'error': 'User ID and plan ID are required'}), 400
            
            result = payment_service.create_paypal_subscription(
                user_id, plan_id, app_id, customer_info
            )
            
            return jsonify({'result': result})
            
        except Exception as e:
            logger.error(f"Error creating PayPal subscription: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/paypal-success', methods=['GET'])
    def paypal_subscription_success():
        """Handle PayPal subscription approval success"""
        try:
            subscription_id = request.args.get('subscription_id')
            
            if not subscription_id:
                return jsonify({'error': 'Missing subscription ID'}), 400
            
            # Activate the subscription
            result = payment_service.activate_paypal_subscription(subscription_id)
            
            # Redirect to success page
            return redirect(f"/subscription-success?subscription_id={subscription_id}")
            
        except Exception as e:
            logger.error(f"Error handling PayPal success: {str(e)}")
            return redirect("/subscription-error")

    @payment_bp.route('/paypal-cancel', methods=['GET'])
    def paypal_subscription_cancel():
        """Handle PayPal subscription approval cancellation"""
        try:
            subscription_id = request.args.get('subscription_id')
            
            if subscription_id:
                # Mark subscription as cancelled
                payment_service.cancel_pending_paypal_subscription(subscription_id)
            
            # Redirect to cancellation page
            return redirect("/subscription-cancelled")
            
        except Exception as e:
            logger.error(f"Error handling PayPal cancel: {str(e)}")
            return redirect("/subscription-error")

    @payment_bp.route('/upgrade', methods=['POST'])
    def upgrade_subscription():
        """Upgrade a subscription to a higher plan"""
        try:
            logger.info("[UPGRADE] Route started")
            data = request.json
            user_id = data.get('user_id')
            subscription_id = data.get('subscription_id')
            new_plan_id = data.get('new_plan_id')
            app_id = data.get('app_id', 'marketfit')
            
            logger.info(f"[UPGRADE3] Params: user={user_id}, sub={subscription_id}, plan={new_plan_id}")
            
            if not all([user_id, subscription_id, new_plan_id]):
                logger.info("[UPGRADE] Missing required parameters")
                return jsonify({'error': 'User ID, subscription ID, and new plan ID are required'}), 400
            
            logger.info("[UPGRADE] Calling payment_service.upgrade_subscription")
            result = payment_service.upgrade_subscription(user_id, subscription_id, new_plan_id, app_id)
            logger.info("[UPGRADE] Payment service returned successfully")
            
            # Check if it's a UPI limitation error
            if result.get('error_type') == 'upi_upgrade_not_supported':
                return jsonify({
                    'error': 'UPI upgrade not supported',
                    'error_type': 'upi_upgrade_not_supported',
                    'message': result.get('message')
                }), 422  # Use 422 for business logic errors
            
            return jsonify({'result': result})
            
        except Exception as e:
            logger.info(f"[UPGRADE] Route exception: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/payment-callback', methods=['GET', 'POST'])
    def payment_callback():
        """Unified payment completion callback handler"""
        try:
            if request.method == 'GET':
                # PayPal return URLs
                payment_type = request.args.get('type')
                token = request.args.get('token')
                
                if payment_type == 'proration' and token:
                    result = payment_service.handle_paypal_proration_completion(token)
                    if result.get('success'):
                        # Redirect to dashboard with success message
                        return redirect('/subscription-dashboard?upgrade=success&message=Your subscription has been upgraded successfully! Proration payment completed.')
                    else:
                        # Redirect to dashboard with error message  
                        return redirect('/subscription-dashboard?upgrade=error&message=There was an issue processing your upgrade payment. Please contact support.')
                
                return redirect('/subscription-dashboard')
            
            elif request.method == 'POST':
                # Handle webhook events as before
                data = request.json
                event_type = data.get('event') or data.get('event_type')
                
                if event_type in ['payment.captured', 'payment_link.paid']:
                    return handle_razorpay_webhook(payment_service)
                elif event_type == 'PAYMENT.CAPTURE.COMPLETED':
                    return handle_paypal_webhook(payment_service)
                else:
                    return jsonify({'status': 'unhandled_event', 'event': event_type})
            
        except Exception as e:
            logger.error(f"Error in payment callback: {str(e)}")
            return redirect('/subscription-dashboard?upgrade=error&message=Payment processing failed. Please contact support.')

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
    def get_subscription_usage():
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