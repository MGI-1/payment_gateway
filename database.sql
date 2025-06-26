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
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  KEY `subscription_id` (`subscription_id`),
  KEY `app_id` (`app_id`),
  KEY `billing_period_start` (`billing_period_start`,`billing_period_end`),
  CONSTRAINT `resource_usage_ibfk_1` FOREIGN KEY (`subscription_id`) REFERENCES `user_subscriptions` (`id`)
)

CREATE TABLE `paypal_webhook_events` (
  `id` int NOT NULL AUTO_INCREMENT,
  `event_id` varchar(100) NOT NULL,
  `event_type` varchar(100) NOT NULL,
  `subscription_id` varchar(100) DEFAULT NULL,
  `paypal_subscription_id` varchar(100) DEFAULT NULL,
  `status` varchar(50) DEFAULT NULL,
  `processed` tinyint(1) DEFAULT '0',
  `webhook_data` json DEFAULT NULL,
  `error` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `processed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `event_id` (`event_id`),
  KEY `paypal_subscription_id` (`paypal_subscription_id`),
  KEY `processed` (`processed`,`created_at`)
)

CREATE TABLE `paypal_access_tokens` (
  `id` int NOT NULL AUTO_INCREMENT,
  `access_token` text NOT NULL,
  `expires_at` datetime NOT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
)

CREATE TABLE `subscription_invoices` (
  `id` varchar(64) NOT NULL,
  `subscription_id` varchar(64) NOT NULL,
  `user_id` varchar(255) NOT NULL,
  `razorpay_invoice_id` varchar(255) DEFAULT NULL,
  `amount` int NOT NULL,
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
)

CREATE TABLE `subscription_plans` (
  `id` varchar(64) NOT NULL,
  `name` varchar(255) NOT NULL,
  `description` text,
  `amount` int NOT NULL,
  `currency` varchar(10) DEFAULT 'INR',
  `interval` varchar(20) NOT NULL,
  `interval_count` int DEFAULT '1',
  `features` json DEFAULT NULL,
  `app_id` varchar(50) DEFAULT NULL,
  `paypal_plan_id` varchar(255) DEFAULT NULL,
  `gateway_support` json DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT '1',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `razorpay_plan_id` varchar(255) DEFAULT NULL,
  `plan_type` enum('domestic','international') NOT NULL DEFAULT 'domestic',
  `payment_gateways` json DEFAULT NULL,
  PRIMARY KEY (`id`)
)

CREATE TABLE `subscription_usage` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` varchar(255) NOT NULL,
  `app_id` varchar(50) NOT NULL DEFAULT 'marketfit',
  `subscription_id` varchar(64) DEFAULT NULL,
  `period_start` date NOT NULL,
  `period_end` date NOT NULL,
  `requests_used` int DEFAULT '0',
  `pages_used` int DEFAULT '0',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_user_period` (`user_id`,`app_id`,`period_start`,`period_end`),
  KEY `user_id` (`user_id`),
  KEY `subscription_id` (`subscription_id`),
  KEY `period_start` (`period_start`,`period_end`)
)

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
);