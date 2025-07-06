"""
Flask routes for payment gateway integration
"""
from flask import Blueprint, request, jsonify, current_app
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
            
            if not user_id or not plan_id:
                return jsonify({'error': 'User ID and Plan ID are required'}), 400
                
            subscription = payment_service.create_subscription(user_id, plan_id, app_id)
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
                return jsonify({'error': 'User ID and resource type are required'}), 400
                
            result = payment_service.check_resource_availability(
                user_id, app_id, resource_type, count
            )
            
            if result:
                return jsonify({'available': True})
            else:
                return jsonify({
                    'available': False,
                    'message': 'You have reached your resource limit for this billing period.'
                })
                
        except Exception as e:
            logger.error(f"Error checking resource availability: {str(e)}")
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
                return jsonify({'error': 'User ID and resource type are required'}), 400
                
            result = payment_service.decrement_resource_quota(
                user_id, app_id, resource_type, count
            )
            
            if result:
                return jsonify({'success': True})
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to decrement resource quota. You may have reached your limit.'
                })
                
        except Exception as e:
            logger.error(f"Error decrementing resource quota: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @payment_bp.route('/resource-quota', methods=['GET'])
    def get_resource_quota():
        """Get resource quota for a user"""
        try:
            user_id = request.args.get('user_id')
            app_id = request.args.get('app_id', 'marketfit')
            
            if not user_id:
                return jsonify({'error': 'User ID is required'}), 400
                
            quota = payment_service.get_resource_quota(user_id, app_id)
            return jsonify({'quota': quota})
            
        except Exception as e:
            logger.error(f"Error getting resource quota: {str(e)}")
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
                return jsonify({'error': 'User ID is required'}), 400
                
            result = payment_service.ensure_user_has_resource_quota(user_id, app_id)
            
            return jsonify({'success': result})
                
        except Exception as e:
            logger.error(f"Error ensuring user has resource quota: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    # Register the blueprint with the app
    app.register_blueprint(payment_bp)
    
    # Log that routes were initialized
    logger.info("Payment gateway routes initialized")