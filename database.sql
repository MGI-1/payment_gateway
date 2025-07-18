CREATE DATABASE battlecards;
USE battlecards;

CREATE TABLE `subscription_plans` (
  `id` varchar(64) NOT NULL,
  `name` varchar(255) NOT NULL,
  `description` text,
  `amount` decimal(10,2) DEFAULT NULL,
  `currency` varchar(10) DEFAULT 'INR',
  `interval` varchar(20) NOT NULL,
  `interval_count` int DEFAULT '1',
  `features` json DEFAULT NULL,
  `app_id` varchar(50) DEFAULT NULL,
  `paypal_plan_id` varchar(255) DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT '1',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `razorpay_plan_id` varchar(255) DEFAULT NULL,
  `plan_type` enum('domestic','international') NOT NULL DEFAULT 'domestic',
  `payment_gateways` json DEFAULT NULL,
  PRIMARY KEY (`id`)
);

CREATE TABLE `user_subscriptions` (
  `id` varchar(64) NOT NULL,
  `user_id` varchar(255) NOT NULL,
  `plan_id` varchar(64) NOT NULL,
  `razorpay_subscription_id` varchar(255) DEFAULT NULL,
  `payment_gateway` varchar(20) DEFAULT 'razorpay',
  `paypal_subscription_id` varchar(255) DEFAULT NULL,
  `gateway_metadata` json DEFAULT NULL,
  `status` varchar(50) NOT NULL,
  `current_period_start` datetime DEFAULT NULL,
  `current_period_end` datetime DEFAULT NULL,
  `app_id` varchar(50) DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `metadata` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  KEY `plan_id` (`plan_id`),
  CONSTRAINT `user_subscriptions_ibfk_1` FOREIGN KEY (`plan_id`) REFERENCES `subscription_plans` (`id`)
  CREATE INDEX `idx_user_subscriptions_user_app` ON `user_subscriptions` (`user_id`, `app_id`);
  CREATE INDEX `idx_user_subscriptions_status` ON `user_subscriptions` (`status`);
  CREATE INDEX `idx_user_subscriptions_razorpay` ON `user_subscriptions` (`razorpay_subscription_id`);
  CREATE INDEX `idx_user_subscriptions_paypal` ON `user_subscriptions` (`paypal_subscription_id`);
);

CREATE TABLE `paypal_access_tokens` (
  `id` int NOT NULL AUTO_INCREMENT,
  `access_token` text NOT NULL,
  `expires_at` datetime NOT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
);

CREATE TABLE `subscription_invoices` (
  `id` varchar(64) NOT NULL,
  `subscription_id` varchar(64) NOT NULL,
  `user_id` varchar(255) NOT NULL,
  `razorpay_invoice_id` varchar(255) DEFAULT NULL,
  `amount` decimal(10,2) DEFAULT NULL,
  `currency` varchar(10) DEFAULT 'INR',
  `status` varchar(50) NOT NULL,
  `payment_id` varchar(255) DEFAULT NULL,
  `invoice_date` datetime DEFAULT NULL,
  `paid_at` datetime DEFAULT NULL,
  `app_id` varchar(50) DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `subscription_id` (`subscription_id`),
  KEY `user_id` (`user_id`),
  CONSTRAINT `subscription_invoices_ibfk_1` FOREIGN KEY (`subscription_id`) REFERENCES `user_subscriptions` (`id`)
);


CREATE TABLE `resource_usage` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` varchar(255) NOT NULL,
  `subscription_id` varchar(64) NOT NULL,
  `app_id` varchar(50) NOT NULL DEFAULT 'marketfit',
  `billing_period_start` datetime NOT NULL,
  `billing_period_end` datetime NOT NULL,
  `document_pages_count` int DEFAULT '0',
  `perplexity_requests_count` int DEFAULT '0',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `document_pages_quota` int DEFAULT '0',
  `perplexity_requests_quota` int DEFAULT '0',
  `requests_quota` int DEFAULT '0',
  `original_document_pages_quota` int DEFAULT '0',
  `original_perplexity_requests_quota` int DEFAULT '0',
  `original_requests_quota` int DEFAULT '0',
  `current_addon_document_pages` int DEFAULT '0',
  `current_addon_perplexity_requests` int DEFAULT '0',
  `current_addon_requests` int DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  KEY `subscription_id` (`subscription_id`),
  KEY `app_id` (`app_id`),
  KEY `billing_period_start` (`billing_period_start`,`billing_period_end`),
  CONSTRAINT `resource_usage_ibfk_1` FOREIGN KEY (`subscription_id`) REFERENCES `user_subscriptions` (`id`)
);

CREATE TABLE `subscription_events_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `event_type` varchar(100) NOT NULL,
  `entity_id` varchar(255) DEFAULT NULL,
  `provider` varchar(20) NOT NULL DEFAULT 'razorpay',
  `user_id` varchar(255) DEFAULT NULL,
  `data` json DEFAULT NULL,
  `processed` tinyint(1) DEFAULT '0',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_entity_id` (`entity_id`),
  KEY `idx_provider` (`provider`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_event_type` (`event_type`),
  KEY `idx_processed` (`processed`)
);

-- Add new audit trail table
CREATE TABLE `subscription_audit_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `subscription_id` varchar(64) NOT NULL,
  `user_id` varchar(255) DEFAULT NULL,
  `action_type` varchar(50) NOT NULL,
  `details` json DEFAULT NULL,
  `initiated_by` varchar(100) DEFAULT 'system',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `subscription_id` (`subscription_id`),
  KEY `action_type` (`action_type`),
  KEY `user_id` (`user_id`),
  KEY `created_at` (`created_at`)
);

-- Add webhook idempotency table
CREATE TABLE `webhook_events_processed` (
  `id` int NOT NULL AUTO_INCREMENT,
  `event_id` varchar(255) NOT NULL,
  `provider` varchar(20) NOT NULL,
  `processed_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_event_provider` (`event_id`, `provider`)
);

-- Add addon purchase table
-- Create resource_addons table with matching character set/collation
CREATE TABLE `resource_addons` (
  `id` varchar(64) NOT NULL,
  `user_id` varchar(255) NOT NULL,
  `subscription_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `app_id` varchar(50) NOT NULL,
  `addon_type` varchar(50) NOT NULL,
  `quantity` int NOT NULL,
  `amount_paid` decimal(10,2) NOT NULL,
  `currency` varchar(10) DEFAULT 'INR',
  `billing_period_start` datetime NOT NULL,
  `billing_period_end` datetime NOT NULL,
  `purchased_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `consumed_quantity` int DEFAULT '0',
  `payment_id` varchar(255) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'active',
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  KEY `subscription_id` (`subscription_id`),
  KEY `app_id` (`app_id`),
  KEY `billing_period` (`billing_period_start`, `billing_period_end`),
  CONSTRAINT `resource_addons_ibfk_1` FOREIGN KEY (`subscription_id`) REFERENCES `user_subscriptions` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;