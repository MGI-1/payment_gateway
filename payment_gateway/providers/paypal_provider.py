"""
PayPal integration provider
Enhanced with full API integration
"""
import logging
import json
import traceback
import requests
import base64
from datetime import datetime, timedelta
from ..config import (
    PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_BASE_URL,
    PAYPAL_RETURN_URL, PAYPAL_CANCEL_URL, FLASK_ENV
)
from ..utils.helpers import generate_id

logger = logging.getLogger('payment_gateway')

class PayPalProvider:
    """
    Provider for PayPal payment gateway integration
    Enhanced with full REST API integration
    """
    
    def __init__(self):
        """Initialize the PayPal client"""
        self.client = None
        self.client_id = PAYPAL_CLIENT_ID
        self.client_secret = PAYPAL_CLIENT_SECRET
        self.base_url = PAYPAL_BASE_URL
        self.is_sandbox = (FLASK_ENV == 'development')
        self.access_token = None
        self.token_expires_at = None
        self.initialized = False
        self.init_client()
    
    def init_client(self):
        """Initialize the PayPal client with credentials"""
        try:
            if not self.client_id or not self.client_secret:
                logger.warning("PayPal credentials not found. PayPal integration will not work.")
                return False
            
            # Test the connection by getting an access token
            test_token = self._get_access_token()
            if test_token:
                self.initialized = True
                logger.info(f"PayPal client initialized for {FLASK_ENV} environment")
                logger.info(f"Using {'sandbox' if self.is_sandbox else 'live'} PayPal API")
                return True
            else:
                logger.error("Failed to obtain PayPal access token during initialization")
                return False
            
        except Exception as e:
            logger.error(f"Failed to initialize PayPal client: {str(e)}")
            logger.error(traceback.format_exc())
            return False
    
    def _get_access_token(self):
        """Get or refresh PayPal access token"""
        try:
            # Check if current token is still valid
            if (self.access_token and self.token_expires_at and 
                datetime.now() < self.token_expires_at - timedelta(minutes=5)):
                return self.access_token
            
            # Request new token
            auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            
            headers = {
                "Accept": "application/json",
                "Accept-Language": "en_US",
                "Authorization": f"Basic {auth}"
            }
            
            data = "grant_type=client_credentials"
            
            response = requests.post(
                f"{self.base_url}/v1/oauth2/token",
                headers=headers,
                data=data,
                timeout=30
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                logger.info("PayPal access token obtained successfully")
                return self.access_token
            else:
                logger.error(f"Failed to get PayPal access token: {response.status_code} {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting PayPal access token: {str(e)}")
            return None
    
    def _make_api_call(self, endpoint, method="GET", data=None):
        """Make authenticated API call to PayPal"""
        try:
            access_token = self._get_access_token()
            if not access_token:
                return {'error': True, 'message': 'Failed to get access token'}
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }
            
            url = f"{self.base_url}{endpoint}"
            
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=30)
            elif method == "POST":
                response = requests.post(url, headers=headers, data=json.dumps(data) if data else None, timeout=30)
            elif method == "PATCH":
                response = requests.patch(url, headers=headers, data=json.dumps(data) if data else None, timeout=30)
            else:
                return {'error': True, 'message': f'Unsupported method: {method}'}
            
            if response.status_code in [200, 201, 204]:
                return response.json() if response.content else {'success': True}
            else:
                logger.error(f"PayPal API error: {response.status_code} {response.text}")
                return {
                    'error': True, 
                    'message': f'PayPal API error: {response.status_code}',
                    'details': response.text
                }
                
        except Exception as e:
            logger.error(f"PayPal API call failed: {str(e)}")
            return {'error': True, 'message': str(e)}
    
    def create_subscription(self, plan_id, customer_info, app_id):
        """
        Create a new subscription in PayPal using REST API
        
        Args:
            plan_id: The PayPal plan ID
            customer_info: Dict with customer details
            app_id: The application ID (marketfit/saleswit)
            
        Returns:
            Dict with subscription details or error
        """
        if not self.initialized:
            return {
                'error': True,
                'message': 'PayPal client not initialized'
            }
        
        try:
            subscription_data = {
                "plan_id": plan_id,
                "subscriber": {
                    "name": {
                        "given_name": customer_info.get('first_name', 'User'),
                        "surname": customer_info.get('last_name', 'Name')
                    },
                    "email_address": customer_info.get('email'),
                },
                "application_context": {
                    "brand_name": customer_info.get('brand_name', 'Your App'),
                    "locale": "en-US",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "SUBSCRIBE_NOW",
                    "payment_method": {
                        "payer_selected": "PAYPAL",
                        "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED"
                    },
                    "return_url": PAYPAL_RETURN_URL,
                    "cancel_url": PAYPAL_CANCEL_URL
                },
                "custom_id": f"{app_id}_{customer_info.get('user_id')}"
            }
            
            logger.info(f"Creating PayPal subscription with plan {plan_id}")
            
            result = self._make_api_call(
                "/v1/billing/subscriptions",
                method="POST",
                data=subscription_data
            )
            
            if result.get('error'):
                return result
            
            # Extract approval URL
            approval_url = self._extract_approval_url(result)
            
            logger.info(f"PayPal subscription created: {result.get('id')}")
            
            return {
                'success': True,
                'subscription_id': result.get('id'),
                'status': result.get('status'),
                'approval_url': approval_url,
                'paypal_response': result,
                'message': 'PayPal subscription created successfully'
            }
            
        except Exception as e:
            logger.error(f"Error creating PayPal subscription: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                'error': True,
                'message': f'PayPal subscription creation failed: {str(e)}'
            }
    
    def verify_subscription(self, subscription_id, payment_info):
        """
        Verify a PayPal subscription payment
        Enhanced to use actual PayPal API
        
        Args:
            subscription_id: The PayPal subscription ID
            payment_info: Additional payment verification info
            
        Returns:
            Dict with verification result or error
        """
        if not self.initialized:
            return {
                'error': True,
                'message': 'PayPal client not initialized'
            }
        
        try:
            logger.info(f"Verifying PayPal subscription {subscription_id}")
            
            # Get subscription details from PayPal
            result = self._make_api_call(f"/v1/billing/subscriptions/{subscription_id}")
            
            if result.get('error'):
                return {
                    'error': True,
                    'message': f'Failed to verify subscription: {result.get("message")}'
                }
            
            status = result.get('status', '').upper()
            
            return {
                'success': True,
                'status': status.lower(),
                'verified': status in ['ACTIVE', 'APPROVED'],
                'subscription_data': result
            }
            
        except Exception as e:
            logger.error(f"Error verifying PayPal subscription: {str(e)}")
            return {
                'error': True,
                'message': f'Verification failed: {str(e)}'
            }
    
    def get_subscription(self, subscription_id):
        """Get PayPal subscription details"""
        if not self.initialized:
            return {'error': True, 'message': 'PayPal not initialized'}
        
        logger.info(f"Fetching PayPal subscription: {subscription_id}")
        return self._make_api_call(f"/v1/billing/subscriptions/{subscription_id}")
    
    def update_subscription(self, subscription_id, new_plan_id, proration_amount=None):
        """
        Update PayPal subscription to new plan with proration
        
        Args:
            subscription_id: PayPal subscription ID
            new_plan_id: New PayPal plan ID to switch to
            proration_amount: Amount to charge/refund for proration
            
        Returns:
            dict: Update result
        """
        if not self.initialized:
            return {'error': True, 'message': 'PayPal not initialized'}
        
        try:
            logger.info(f"Updating PayPal subscription {subscription_id} to plan {new_plan_id}")
            
            # Prepare revision data
            revision_data = {
                "plan_id": new_plan_id,
                "application_context": {
                    "user_action": "SUBSCRIBE_NOW",
                    "return_url": PAYPAL_RETURN_URL,
                    "cancel_url": PAYPAL_CANCEL_URL
                }
            }
            
            # Add proration if specified
            if proration_amount and proration_amount > 0:
                revision_data["proration"] = {
                    "prorate": True,
                    "outstanding_balance": {
                        "currency_code": "USD",  # You may want to make this configurable
                        "value": str(proration_amount)
                    }
                }
            
            # Call PayPal revision API
            result = self._make_api_call(
                f"/v1/billing/subscriptions/{subscription_id}/revise",
                method="POST",
                data=revision_data
            )
            
            if result.get('error'):
                return result
            
            # PayPal subscription revision was successful
            logger.info(f"PayPal subscription {subscription_id} updated successfully")
            
            return {
                'success': True,
                'subscription_id': subscription_id,
                'new_plan_id': new_plan_id,
                'proration_amount': proration_amount,
                'paypal_response': result,
                'approval_url': self._extract_approval_url(result),
                'message': 'Subscription updated successfully'
            }
            
        except Exception as e:
            logger.error(f"Error updating PayPal subscription: {str(e)}")
            return {'error': True, 'message': str(e)}
    
    def cancel_subscription(self, subscription_id, reason="User requested cancellation"):
        """Cancel PayPal subscription"""
        if not self.initialized:
            return {'error': True, 'message': 'PayPal not initialized'}
        
        try:
            logger.info(f"Cancelling PayPal subscription: {subscription_id}")
            
            cancel_data = {
                "reason": reason
            }
            
            result = self._make_api_call(
                f"/v1/billing/subscriptions/{subscription_id}/cancel",
                method="POST",
                data=cancel_data
            )
            
            if result.get('error'):
                return result
            
            logger.info(f"PayPal subscription {subscription_id} cancelled successfully")
            return {
                'success': True,
                'subscription_id': subscription_id,
                'message': 'Subscription cancelled successfully'
            }
            
        except Exception as e:
            logger.error(f"Error cancelling PayPal subscription: {str(e)}")
            return {'error': True, 'message': str(e)}
    
    def update_subscription_plan_only(self, subscription_id, new_plan_id):
        """Update PayPal subscription plan with authorization check"""
        if not self.initialized:
            return {'error': True, 'message': 'PayPal not initialized'}
        
        try:
            revision_data = {
                "plan_id": new_plan_id,
                "application_context": {
                    "user_action": "SUBSCRIBE_NOW",
                    "return_url": PAYPAL_RETURN_URL,
                    "cancel_url": PAYPAL_CANCEL_URL
                }
            }
            
            result = self._make_api_call(
                f"/v1/billing/subscriptions/{subscription_id}/revise",
                method="POST",
                data=revision_data
            )
            
            if result.get('error'):
                return result
            
            approval_url = self._extract_approval_url(result)
            status = result.get('status', '')
            
            approval_reason = None
            if approval_url:
                if 'APPROVAL_PENDING' in status:
                    approval_reason = "PayPal account re-authorization required"
                elif 'PENDING' in status:
                    approval_reason = "Card authorization required for new amount"
                else:
                    approval_reason = "Additional authorization required"
            
            return {
                'success': True,
                'requires_approval': approval_url is not None,
                'approval_url': approval_url,
                'approval_reason': approval_reason,
                'status': status,
                'message': f'Plan updated. {approval_reason}' if approval_url else 'Plan updated successfully.'
            }
            
        except Exception as e:
            logger.error(f"Error updating PayPal subscription plan: {str(e)}")
            return {'error': True, 'message': str(e)}

    def create_one_time_payment(self, payment_data):
        """Create one-time payment order for proration"""
        if not self.initialized:
            return {'error': True, 'message': 'PayPal not initialized'}
        
        try:
            order_data = {
                "intent": "CAPTURE",
                "purchase_units": [{
                    "amount": {
                        "currency_code": payment_data.get('currency', 'USD'),
                        "value": str(payment_data['amount'])
                    },
                    "description": payment_data.get('description', 'Upgrade proration payment'),
                    "custom_id": f"sub_{payment_data.get('metadata', {}).get('subscription_id')}"
                }],
                "application_context": {
                    "return_url": f"{PAYPAL_RETURN_URL}?type=proration",
                    "cancel_url": f"{PAYPAL_CANCEL_URL}?type=proration"
                }
            }
            
            result = self._make_api_call(
                "/v2/checkout/orders",
                method="POST",
                data=order_data
            )
            
            if result.get('error'):
                return result
            
            return {
                'success': True,
                'order_id': result.get('id'),
                'approval_url': self._extract_approval_url(result),
                'status': result.get('status'),
                'paypal_response': result
            }
            
        except Exception as e:
            logger.error(f"Error creating PayPal one-time payment: {str(e)}")
            return {'error': True, 'message': str(e)}

    def capture_order_payment(self, order_id):
        """Capture payment for completed order"""
        if not self.initialized:
            return {'error': True, 'message': 'PayPal not initialized'}
        
        try:
            result = self._make_api_call(
                f"/v2/checkout/orders/{order_id}/capture",
                method="POST"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error capturing PayPal order: {str(e)}")
            return {'error': True, 'message': str(e)}    
    
    def _extract_approval_url(self, paypal_response):
        """Extract approval URL from PayPal response if present"""
        try:
            links = paypal_response.get('links', [])
            for link in links:
                if link.get('rel') == 'approve':
                    return link.get('href')
            return None
        except:
            return None