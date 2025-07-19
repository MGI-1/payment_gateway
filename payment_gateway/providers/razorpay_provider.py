"""
Razorpay integration provider
"""
import razorpay
import json
import logging
import traceback
from datetime import datetime, timedelta
from ..config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET

logger = logging.getLogger('payment_gateway')

class RazorpayProvider:
    """
    Provider for Razorpay payment gateway integration
    """
    
    def __init__(self):
        """Initialize the Razorpay client"""
        self.client = None
        self.initialized = False
        self.init_client()
    
    def init_client(self):
        """Initialize the Razorpay client with credentials"""
        try:
            if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
                logger.warning("Razorpay credentials not found. Razorpay integration will not work.")
                return False
                
            self.client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
            self.initialized = True
            logger.info("Razorpay client initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Razorpay client: {str(e)}")
            logger.error(traceback.format_exc())
            return False
    
    def create_subscription(self, plan_id, customer_info, app_id, additional_notes=None):
        """
        Create a new subscription in Razorpay
        
        Args:
            plan_id: The plan ID in Razorpay
            customer_info: Dict with customer details
            app_id: The application ID (marketfit/saleswit)
            additional_notes: Additional notes to include
            
        Returns:
            Dict with subscription details or error
        """
        if not self.initialized or not self.client:
            return {
                'error': True,
                'message': 'Razorpay client not initialized'
            }
        
        try:
            user_id = customer_info.get('user_id')
            
            # Create the notes object
            notes = {
                'user_id': user_id,
                'app_id': app_id
            }
            
            # Add any additional notes
            if additional_notes and isinstance(additional_notes, dict):
                notes.update(additional_notes)
            
            # DEBUG: Log exactly what we're sending to Razorpay
            logger.info(f"[RAZORPAY DEBUG] customer_info received: {customer_info}")
            logger.info(f"[RAZORPAY DEBUG] customer_info types: {[(k, type(v)) for k, v in customer_info.items()]}")
            logger.info(f"[RAZORPAY DEBUG] app_id: {app_id}, type: {type(app_id)}")
            logger.info(f"[RAZORPAY DEBUG] notes being sent: {notes}")
            logger.info(f"[RAZORPAY DEBUG] notes types: {[(k, type(v)) for k, v in notes.items()]}")
            
            # Create the Razorpay subscription
            subscription_data = {
                'plan_id': plan_id,
                'customer_notify': True,
                'quantity': 1,
                'total_count': 12,
                'notes': notes
            }
            
            logger.info(f"[RAZORPAY DEBUG] Full subscription_data: {subscription_data}")    
            logger.info(f"Creating Razorpay subscription for user {user_id} with plan {plan_id}")
            razorpay_subscription = self.client.subscription.create(subscription_data)
            
            return {
                'id': razorpay_subscription.get('id'),
                'status': razorpay_subscription.get('status'),
                'short_url': razorpay_subscription.get('short_url'),
                'data': razorpay_subscription
            }
            
        except Exception as e:
            logger.error(f"Error creating Razorpay subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                'error': True,
                'message': str(e)
            }
    
    def cancel_subscription(self, subscription_id, cancel_at_cycle_end=True):
        """
        Cancel a subscription in Razorpay
        
        Args:
            subscription_id: The Razorpay subscription ID
            cancel_at_cycle_end: Whether to cancel at end of billing cycle
            
        Returns:
            Dict with cancellation result or error
        """
        if not self.initialized or not self.client:
            return {
                'error': True,
                'message': 'Razorpay client not initialized'
            }
        
        try:
            logger.info(f"Cancelling Razorpay subscription: {subscription_id}")
            
            # Cancel the subscription
            result = self.client.subscription.cancel(
                subscription_id,
                {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0}
            )
            
            return {
                'success': True,
                'status': result.get('status'),
                'data': result
            }
            
        except Exception as e:
            logger.error(f"Error cancelling Razorpay subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                'error': True,
                'message': str(e)
            }
    
    def fetch_subscription(self, subscription_id):
        """
        Fetch a subscription from Razorpay
        
        Args:
            subscription_id: The Razorpay subscription ID
            
        Returns:
            Dict with subscription details or error
        """
        if not self.initialized or not self.client:
            return {
                'error': True,
                'message': 'Razorpay client not initialized'
            }
        
        try:
            logger.info(f"Fetching Razorpay subscription: {subscription_id}")
            
            # Fetch the subscription
            subscription = self.client.subscription.fetch(subscription_id)
            
            return {
                'success': True,
                'status': subscription.get('status'),
                'data': subscription
            }
            
        except Exception as e:
            logger.error(f"Error fetching Razorpay subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                'error': True,
                'message': str(e)
            }