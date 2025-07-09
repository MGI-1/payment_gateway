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
        return mysql.connector.connect(**self.db_config)
    
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
    
    def log_event(self, event_type, entity_id, user_id, data, provider='razorpay', processed=False):
        """Log a payment event for debugging and auditing"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if provider == 'razorpay':
                razorpay_entity_id = entity_id
                paypal_entity_id = None
            else:
                razorpay_entity_id = None
                paypal_entity_id = entity_id
            
            # Convert data to JSON string if it's a dict
            data_json = json.dumps(data) if isinstance(data, dict) else data
            
            cursor.execute(f'''
                INSERT INTO {DB_TABLE_SUBSCRIPTION_EVENTS}
                (event_type, razorpay_entity_id, user_id, data, processed)
                VALUES (%s, %s, %s, %s, %s)
            ''', (event_type, razorpay_entity_id, user_id, data_json, processed))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
        
        except Exception as e:
            logger.error(f"Error logging event: {str(e)}")
            logger.error(traceback.format_exc())
            return False