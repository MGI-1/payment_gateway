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
            
            # Create subscription plans table
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {DB_TABLE_SUBSCRIPTION_PLANS} (
                    id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    amount INT NOT NULL,
                    currency VARCHAR(10) DEFAULT 'INR',
                    `interval` VARCHAR(20) NOT NULL,
                    interval_count INT DEFAULT 1,
                    features JSON,
                    app_id VARCHAR(50),
                    plan_type VARCHAR(50) DEFAULT 'domestic',
                    payment_gateways JSON,
                    paypal_plan_id VARCHAR(255),
                    razorpay_plan_id VARCHAR(255),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create user subscriptions table
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {DB_TABLE_USER_SUBSCRIPTIONS} (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    plan_id VARCHAR(64) NOT NULL,
                    razorpay_subscription_id VARCHAR(255),
                    paypal_subscription_id VARCHAR(255),
                    status VARCHAR(50) NOT NULL,
                    current_period_start DATETIME,
                    current_period_end DATETIME,
                    app_id VARCHAR(50),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    metadata JSON,
                    INDEX (user_id),
                    INDEX (plan_id),
                    FOREIGN KEY (plan_id) REFERENCES {DB_TABLE_SUBSCRIPTION_PLANS}(id)
                )
            ''')
            
            # Create subscription invoices table
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {DB_TABLE_SUBSCRIPTION_INVOICES} (
                    id VARCHAR(64) PRIMARY KEY,
                    subscription_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(255) NOT NULL,
                    razorpay_invoice_id VARCHAR(255),
                    paypal_invoice_id VARCHAR(255),
                    amount INT NOT NULL,
                    currency VARCHAR(10) DEFAULT 'INR',
                    status VARCHAR(50) NOT NULL,
                    payment_id VARCHAR(255),
                    invoice_date DATETIME,
                    paid_at DATETIME,
                    app_id VARCHAR(50),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX (subscription_id),
                    INDEX (user_id),
                    FOREIGN KEY (subscription_id) REFERENCES {DB_TABLE_USER_SUBSCRIPTIONS}(id)
                )
            ''')
            
            # Create subscription events log table for debugging
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {DB_TABLE_SUBSCRIPTION_EVENTS} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    event_type VARCHAR(100) NOT NULL,
                    razorpay_entity_id VARCHAR(255),
                    paypal_entity_id VARCHAR(255),
                    user_id VARCHAR(255),
                    data JSON,
                    processed BOOLEAN DEFAULT FALSE,
                    error TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create resource usage table
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {DB_TABLE_RESOURCE_USAGE} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    subscription_id VARCHAR(64) NOT NULL,
                    app_id VARCHAR(50) NOT NULL,
                    billing_period_start DATETIME NOT NULL,
                    billing_period_end DATETIME NOT NULL,
                    document_pages_count INT DEFAULT 0,
                    perplexity_requests_count INT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX (user_id),
                    INDEX (subscription_id),
                    INDEX (app_id),
                    FOREIGN KEY (subscription_id) REFERENCES {DB_TABLE_USER_SUBSCRIPTIONS}(id)
                )
            ''')
            
            # Create free plans if they don't exist
            cursor.execute(f'''
                INSERT IGNORE INTO {DB_TABLE_SUBSCRIPTION_PLANS}
                (id, name, description, amount, currency, `interval`, interval_count, features, app_id, payment_gateways)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                'plan_free_marketfit', 'Free Plan', 'Basic access to MarketFit features', 0, 'INR', 
                'month', 1, json.dumps({
                    "documents": 5, 
                    "battlecards": 10, 
                    "users": 1,
                    "document_pages": 50,
                    "perplexity_requests": 20
                }), 'marketfit', json.dumps(['razorpay']),
                
                'plan_free_saleswit', 'Free Plan', 'Basic access to SalesWit features', 0, 'INR', 
                'month', 1, json.dumps({
                    "documents": 5, 
                    "queries": 10, 
                    "users": 1,
                    "document_pages": 50,
                    "perplexity_requests": 20
                }), 'saleswit', json.dumps(['razorpay'])
            ))
            
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
                (event_type, razorpay_entity_id, paypal_entity_id, user_id, data, processed)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (event_type, razorpay_entity_id, paypal_entity_id, user_id, data_json, processed))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True
        
        except Exception as e:
            logger.error(f"Error logging event: {str(e)}")
            logger.error(traceback.format_exc())
            return False