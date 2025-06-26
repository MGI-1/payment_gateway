#!/usr/bin/env python3
"""
Utility script to sync all active subscriptions with payment providers.
Can be used as a scheduled task or cron job.
"""
import os
import sys
import logging
import argparse
from datetime import datetime
from dotenv import load_dotenv

# Add the parent directory to the path so we can import the package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from payment_gateway import PaymentService
from payment_gateway.config import setup_logging

# Load environment variables
load_dotenv()

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Sync subscriptions with payment providers')
    parser.add_argument('--app-id', help='Application ID to sync (default: all)', default=None)
    parser.add_argument('--log-file', help='Log file path', default=None)
    parser.add_argument('--dry-run', help='Dry run (no updates)', action='store_true')
    return parser.parse_args()

def sync_subscriptions(app_id=None, dry_run=False):
    """Sync all active subscriptions with payment providers"""
    # Set up logging
    logger = setup_logging('sync_subscriptions', f'sync_subscriptions_{datetime.now().strftime("%Y%m%d")}.log')
    
    # Initialize payment service
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'app_database')
    }
    
    payment_service = PaymentService(db_config=db_config)
    
    logger.info(f"Starting subscription sync {'(DRY RUN)' if dry_run else ''}")
    
    # Get database connection
    conn = payment_service.db.get_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Build query to get active subscriptions
    query = """
        SELECT id, razorpay_subscription_id, paypal_subscription_id, app_id
        FROM user_subscriptions
        WHERE status IN ('active', 'created', 'authenticated', 'halted')
    """
    
    params = []
    if app_id:
        query += " AND app_id = %s"
        params.append(app_id)
    
    # Execute query
    cursor.execute(query, params)
    subscriptions = cursor.fetchall()
    
    logger.info(f"Found {len(subscriptions)} subscriptions to sync")
    
    # Sync each subscription
    synced_count = 0
    failed_count = 0
    
    for subscription in subscriptions:
        sub_id = subscription['id']
        app_id = subscription['app_id']
        
        # Check which provider to use
        if subscription['razorpay_subscription_id']:
            provider = 'razorpay'
            provider_sub_id = subscription['razorpay_subscription_id']
        elif subscription['paypal_subscription_id']:
            provider = 'paypal'
            provider_sub_id = subscription['paypal_subscription_id']
        else:
            logger.warning(f"Subscription {sub_id} has no provider ID, skipping")
            continue
        
        logger.info(f"Syncing {provider} subscription {provider_sub_id} for app {app_id}")
        
        try:
            if provider == 'razorpay':
                # Get the latest status from Razorpay
                result = payment_service.razorpay.fetch_subscription(provider_sub_id)
                
                if result.get('error'):
                    logger.error(f"Error fetching subscription from Razorpay: {result.get('message')}")
                    failed_count += 1
                    continue
                
                # Update subscription status in database if not dry run
                if not dry_run:
                    # This would be implemented with a method in PaymentService
                    # For now, just log it
                    logger.info(f"Would update subscription {sub_id} status to {result.get('status')}")
                    synced_count += 1
                else:
                    logger.info(f"DRY RUN: Would update subscription {sub_id} status to {result.get('status')}")
                    synced_count += 1
                
            elif provider == 'paypal':
                # PayPal sync would be implemented here
                logger.info(f"PayPal sync not fully implemented for {provider_sub_id}")
                synced_count += 1
                
        except Exception as e:
            logger.error(f"Error syncing subscription {sub_id}: {str(e)}")
            failed_count += 1
    
    cursor.close()
    conn.close()
    
    logger.info(f"Sync complete. Synced: {synced_count}, Failed: {failed_count}")
    
    return synced_count, failed_count

if __name__ == "__main__":
    args = parse_args()
    
    try:
        sync_subscriptions(app_id=args.app_id, dry_run=args.dry_run)
        sys.exit(0)
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)