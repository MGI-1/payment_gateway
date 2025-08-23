LOCK TABLES `subscription_plans` WRITE;

INSERT INTO `subscription_plans` (
  `id`, `name`, `description`, `amount`, `currency`, `interval`, `interval_count`, 
  `features`, `app_id`, `paypal_plan_id`, `is_active`, `created_at`, `razorpay_plan_id`, 
  `plan_type`, `payment_gateways`
) VALUES 
('P-4N115743P3276984DNCU64FA', 'Gold', 'Gold access to MarketFit', 143.40, 'USD', 'year', 1, '{"document_pages": 350, "perplexity_requests": 10}', 'marketfit', 'P-4N115743P3276984DNCU64FA', 1, '2025-06-06 20:34:23', 'plan_R7wisIey0wboxS', 'international', '["paypal", "razorpay"]'),
('P-67B55730S0107231FNCU63HI', 'Gold', 'Gold access to MarketFit', 14.95, 'USD', 'month', 1, '{"document_pages": 350, "perplexity_requests": 10}', 'marketfit', 'P-67B55730S0107231FNCU63HI', 1, '2025-06-06 20:34:23', 'plan_R7wgLDdI6fS84Y', 'international', '["paypal", "razorpay"]'),
('P-7SU50032PW000311GNCU7CPI', 'Platinum', 'Platinum access to MarketFit', 215.40, 'USD', 'year', 1, '{"document_pages": 800, "perplexity_requests": 20}', 'marketfit', 'P-7SU50032PW000311GNCU7CPI', 1, '2025-06-06 20:34:23', 'plan_R7wiCVwjGqDBsd', 'international', '["paypal", "razorpay"]'),
('P-3JF948768R0522941NCU7BSQ', 'Platinum', 'Platinum access to MarketFit', 21.95, 'USD', 'month', 1, '{"document_pages": 800, "perplexity_requests": 20}', 'marketfit', 'P-3JF948768R0522941NCU7BSQ', 1, '2025-06-06 20:34:23', 'plan_R7whBuES2qsXNT', 'international', '["paypal", "razorpay"]'),
('plan_R7wc1HX1Ec7Kjt', 'Gold', 'Gold access to MarketFit', 1199.00, 'INR', 'month', 1, '{"document_pages": 350, "perplexity_requests": 10}', 'marketfit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7wc1HX1Ec7Kjt', 'domestic', '["razorpay"]'),
('plan_R7wcATJQzoyhkb', 'Gold', 'Gold access to SalesWit', 1199.00, 'INR', 'month', 1, '{"requests": 10}', 'saleswit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7wcATJQzoyhkb', 'domestic', '["razorpay"]'),
('plan_R7wcSpKqLkC6Tg', 'Platinum', 'Platinum access to SalesWit', 1799.00, 'INR', 'month', 1, '{"requests": 20}', 'saleswit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7wcSpKqLkC6Tg', 'domestic', '["razorpay"]'),
('plan_R7wcZAnzGuAtLx', 'Platinum', 'Platinum access to MarketFit', 1799.00, 'INR', 'month', 1, '{"document_pages": 800, "perplexity_requests": 20}', 'marketfit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7wcZAnzGuAtLx', 'domestic', '["razorpay"]'),
('plan_R7wdbighbHnJlG', 'Platinum', 'Platinum access to SalesWit', 17988.00, 'INR', 'year', 1, '{"requests": 20}', 'saleswit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7wdbighbHnJlG', 'domestic', '["razorpay"]'),
('plan_R7wdVfrIMvICrU', 'Platinum', 'Platinum access to MarketFit', 17988.00, 'INR', 'year', 1, '{"document_pages": 800, "perplexity_requests": 20}', 'marketfit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7wdVfrIMvICrU', 'domestic', '["razorpay"]'),
('plan_R7we00iLGEvGMT', 'Gold', 'Gold access to SalesWit', 11988.00, 'INR', 'year', 1, '{"requests": 10}', 'saleswit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7we00iLGEvGMT', 'domestic', '["razorpay"]'),
('plan_R7we67gj8jznOu', 'Gold', 'Gold access to MarketFit', 11988.00, 'INR', 'year', 1, '{"document_pages": 350, "perplexity_requests": 10}', 'marketfit', NULL, 1, '2025-06-06 20:34:23', 'plan_R7we67gj8jznOu', 'domestic', '["razorpay"]'),
('P-0K378342A2456682JNCU7DNI', 'Gold', 'Gold access to SalesWit', 143.40, 'USD', 'year', 1, '{"requests": 10}', 'saleswit', 'P-0K378342A2456682JNCU7DNI', 1, '2025-06-06 20:34:23', 'plan_R7wimSjkmFd9IP', 'international', '["paypal", "razorpay"]'),
('P-70861846X3237140WNCU7C7I', 'Gold', 'Gold access to SalesWit', 14.95, 'USD', 'month', 1, '{"requests": 10}', 'saleswit', 'P-70861846X3237140WNCU7C7I', 1, '2025-06-06 20:34:23', 'plan_R7wgScZ1N4CZr3', 'international', '["paypal", "razorpay"]'),
('P-6GE78381CU6774717NCU7ECA', 'Platinum', 'Platinum access to SalesWit', 215.40, 'USD', 'year', 1, '{"requests": 20}', 'saleswit', 'P-6GE78381CU6774717NCU7ECA', 1, '2025-06-06 20:34:23', 'plan_R7wiIfHryzGkLM', 'international', '["paypal", "razorpay"]'),
('P-28P544701F5931341NCU7DXA', 'Platinum', 'Platinum access to SalesWit', 21.95, 'USD', 'month', 1, '{"requests": 20}', 'saleswit', 'P-28P544701F5931341NCU7DXA', 1, '2025-06-06 20:34:23', 'plan_R7wh61mTSYnF08', 'international', '["paypal", "razorpay"]');

UNLOCK TABLES;
