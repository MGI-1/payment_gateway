"""
Database utilities for the payment gateway package.
"""
import mysql.connector
import json
import logging
import traceback
from datetime import datetime
from .config import (
    DEFAULT_DB_CONFIG, 
    DB_TABLE_SUBSCRIPTION_PLANS,
    DB_TABLE_USER_SUBSCRIPTIONS,
    DB_TABLE_SUBSCRIPTION_INVOICES,
    DB_TABLE_SUBSCRIPTION_EVENTS,
    DB_TABLE_RESOURCE_USAGE
)

logger = logging.getLogger('payment_gateway')

class DatabaseManager:
    """
    Database manager for payment gateway operations.
    Handles connections and table initialization.
    """
    
    def __init__(self, db_config=None):
        """Initialize the database manager"""
        self.db_config = db_config or DEFAULT_DB_CONFIG
        
    def get_connection(self):
        """Get a new database connection"""
        # Create a copy of config to avoid modifying the original
        config = self.db_config.copy()
        # Set buffered=True, overriding any existing value
        config['buffered'] = True
        return mysql.connector.connect(**config)
    
    def init_tables(self):
        """Initialize database tables required for payment processing"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info("Payment gateway database tables initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error initializing database tables: {str(e)}")
            logger.error(traceback.format_exc())
            return False
        
    def log_event(self, event_type, entity_id, user_id, data, provider=None, processed=False):
        """Log a payment event for debugging and auditing"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Convert data to JSON string if it's a dict
            data_json = json.dumps(data) if isinstance(data, dict) else data
            
            # Ensure provider is never null
            if provider is None:
                # Determine provider based on event type if possible
                if 'razorpay' in str(event_type).lower():
                    provider = 'razorpay'
                elif 'paypal' in str(event_type).lower():
                    provider = 'paypal'
                elif 'admin' in str(event_type).lower() or str(user_id).lower() == 'admin':
                    provider = 'admin'
                else:
                    provider = 'system'  # Default fallback
            
            logger.debug(f"Logging event: {event_type} with provider: {provider}")
            
            cursor.execute(f'''
                INSERT INTO {DB_TABLE_SUBSCRIPTION_EVENTS}
                (event_type, entity_id, provider, user_id, data, processed, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ''', (event_type, entity_id, provider, user_id, data_json, processed))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
        
        except Exception as e:
            logger.error(f"Error logging event: {str(e)}")
            logger.error(traceback.format_exc())
            return False
        
    def log_subscription_action(self, subscription_id, action_type, details, initiated_by='system'):
        """Log subscription changes for audit trail"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO subscription_audit_log 
                (subscription_id, action_type, details, initiated_by, created_at)
                VALUES (%s, %s, %s, %s, NOW())
            """, (subscription_id, action_type, json.dumps(details), initiated_by))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Logged subscription action: {action_type} for {subscription_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error logging subscription action: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def is_event_processed(self, event_id, provider):
        """Check if webhook event has already been processed"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id FROM webhook_events_processed 
                WHERE event_id = %s AND provider = %s
            """, (event_id, provider))
            
            result = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Error checking event processed status: {str(e)}")
            return False

    def mark_event_processed(self, event_id, provider):
        """Mark webhook event as processed"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT IGNORE INTO webhook_events_processed 
                (event_id, provider, processed_at)
                VALUES (%s, %s, NOW())
            """, (event_id, provider))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
            
        except Exception as e:
            logger.error(f"Error marking event as processed: {str(e)}")
            logger.error(traceback.format_exc())
            return False