-- MySQL dump 10.13  Distrib 8.0.42, for Win64 (x86_64)
--
-- Host: localhost    Database: battlecards
-- ------------------------------------------------------
-- Server version	8.0.40

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Dumping data for table `subscription_plans`
--

LOCK TABLES `subscription_plans` WRITE;
/*!40000 ALTER TABLE `subscription_plans` DISABLE KEYS */;
INSERT INTO `subscription_plans` VALUES ('P-4N115743P3276984DNCU64FA','Gold','Standard access to MarketFit features',143.40,'USD','year',1,'{\"document_pages\": 350, \"perplexity_requests\": 10}','marketfit','P-4N115743P3276984DNCU64FA',1,'2025-06-06 20:34:23','plan_R7wisIey0wboxS','international','[\"paypal\", \"razorpay\"]'),('P-67B55730S0107231FNCU63HI','Gold','Standard access to MarketFit features',14.95,'USD','month',1,'{\"document_pages\": 350, \"perplexity_requests\": 10}','marketfit','P-67B55730S0107231FNCU63HI',1,'2025-06-06 20:34:23','plan_R7wgLDdI6fS84Y','international','[\"paypal\", \"razorpay\"]'),('P-7SU50032PW000311GNCU7CPI','Platinum','Standard access to MarketFit features',215.40,'USD','year',1,'{\"document_pages\": 800, \"perplexity_requests\": 20}','marketfit','P-7SU50032PW000311GNCU7CPI',1,'2025-06-06 20:34:23','plan_R7wiCVwjGqDBsd','international','[\"paypal\", \"razorpay\"]'),('P-3JF948768R0522941NCU7BSQ','Platinum','Standard access to MarketFit features',21.95,'USD','month',1,'{\"document_pages\": 800, \"perplexity_requests\": 20}','marketfit','P-3JF948768R0522941NCU7BSQ',1,'2025-06-06 20:34:23','plan_R7whBuES2qsXNT','international','[\"paypal\", \"razorpay\"]'),('plan_R7wc1HX1Ec7Kjt','Gold','Standard access to MarketFit features',1199.00,'INR','month',1,'{\"document_pages\": 350, \"perplexity_requests\": 10}','marketfit',NULL,1,'2025-06-06 20:34:23','plan_R7wc1HX1Ec7Kjt','domestic','[\"razorpay\"]'),('plan_R7wcATJQzoyhkb','Gold','Standard access to SalesWit features',1199.00,'INR','month',1,'{\"requests\": 10}','saleswit',NULL,1,'2025-06-06 20:34:23','plan_R7wcATJQzoyhkb','domestic','[\"razorpay\"]'),('plan_R7wcSpKqLkC6Tg','Platinum','Standard access to SalesWit features',1799.00,'INR','month',1,'{\"requests\": 20}','saleswit',NULL,1,'2025-06-06 20:34:23','plan_R7wcSpKqLkC6Tg','domestic','[\"razorpay\"]'),('plan_R7wcZAnzGuAtLx','Platinum','Standard access to MarketFit features',1799.00,'INR','month',1,'{\"document_pages\": 800, \"perplexity_requests\": 20}','marketfit',NULL,1,'2025-06-06 20:34:23','plan_R7wcZAnzGuAtLx','domestic','[\"razorpay\"]'),('plan_R7wdbighbHnJlG','Platinum','Standard access to SalesWit features',17988.00,'INR','year',1,'{\"requests\": 20}','saleswit',NULL,1,'2025-06-06 20:34:23','plan_R7wdbighbHnJlG','domestic','[\"razorpay\"]'),('plan_R7wdVfrIMvICrU','Platinum','Standard access to MarketFit features',17988.00,'INR','year',1,'{\"document_pages\": 800, \"perplexity_requests\": 20}','marketfit',NULL,1,'2025-06-06 20:34:23','plan_R7wdVfrIMvICrU','domestic','[\"razorpay\"]'),('plan_R7we00iLGEvGMT','Gold','Standard access to SalesWit features',11988.00,'INR','year',1,'{\"requests\": 10}','saleswit',NULL,1,'2025-06-06 20:34:23','plan_R7we00iLGEvGMT','domestic','[\"razorpay\"]'),('plan_R7we67gj8jznOu','Gold','Standard access to MarketFit features',11988.00,'INR','year',1,'{\"document_pages\": 350, \"perplexity_requests\": 10}','marketfit',NULL,1,'2025-06-06 20:34:23','plan_R7we67gj8jznOu','domestic','[\"razorpay\"]'),('P-0K378342A2456682JNCU7DNI','Gold','Standard access to SalesWit features',143.40,'USD','year',1,'{\"requests\": 10}','saleswit','P-0K378342A2456682JNCU7DNI',1,'2025-06-06 20:34:23','plan_R7wimSjkmFd9IP','international','[\"paypal\", \"razorpay\"]'),('P-70861846X3237140WNCU7C7I','Gold','Standard access to SalesWit features',14.95,'USD','month',1,'{\"requests\": 10}','saleswit','P-70861846X3237140WNCU7C7I',1,'2025-06-06 20:34:23','plan_R7wgScZ1N4CZr3','international','[\"paypal\", \"razorpay\"]'),('P-6GE78381CU6774717NCU7ECA','Platinum','Standard access to SalesWit features',215.40,'USD','year',1,'{\"requests\": 20}','saleswit','P-6GE78381CU6774717NCU7ECA',1,'2025-06-06 20:34:23','plan_R7wiIfHryzGkLM','international','[\"paypal\", \"razorpay\"]'),('P-28P544701F5931341NCU7DXA','Platinum','Standard access to SalesWit features',21.95,'USD','month',1,'{\"requests\": 20}','saleswit','P-28P544701F5931341NCU7DXA',1,'2025-06-06 20:34:23','plan_R7wh61mTSYnF08','international','[\"paypal\", \"razorpay\"]');
/*!40000 ALTER TABLE `subscription_plans` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2025-07-04  4:24:29
