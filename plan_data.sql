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
INSERT INTO `subscription_plans` VALUES ('P-00B61108MC2877013NBPEHZA','Gold','Standard access to MarketFit features',15,'USD','month',1,'{\"document_pages\": 1000, \"perplexity_requests\": 10}','marketfit','P-00B61108MC2877013NBPEHZA','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_Qowq5zgTon5evd','international','[\"paypal\", \"razorpay\"]'),('P-4R292538MH3548302NBPL3KY','Gold','Standard access to SalesWit features',15,'USD','month',1,'{\"requests\": 10}','saleswit','P-4R292538MH3548302NBPL3KY','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_QowqWo6rb1w47S','international','[\"paypal\", \"razorpay\"]'),('P-50Y28795S1820243BNBPEG6A','Platinum','Standard access to MarketFit features',18,'USD','year',1,'{\"document_pages\": 3000, \"perplexity_requests\": 20}','marketfit','P-50Y28795S1820243BNBPEG6A','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_QoworJ8x94duYC','international','[\"paypal\", \"razorpay\"]'),('P-5EN298909A890245WNBPL34Y','Platinum','Standard access to SalesWit features',18,'USD','year',1,'{\"requests\": 20}','saleswit','P-5EN298909A890245WNBPL34Y','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_QowraUbFe1x95j','international','[\"paypal\", \"razorpay\"]'),('P-5H619884MX0105421NBPEF6A','Gold','Standard access to MarketFit features',12,'USD','year',1,'{\"document_pages\": 1000, \"perplexity_requests\": 10}','marketfit','P-5H619884MX0105421NBPEF6A','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_QoslGn6XUcNbMp','international','[\"paypal\", \"razorpay\"]'),('P-7KJ31869GL5164517NBPL4NY','Platinum','Standard access to SalesWit features',22,'USD','month',1,'{\"requests\": 20}','saleswit','P-7KJ31869GL5164517NBPL4NY','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_QowruSfuOXhKFS','international','[\"paypal\", \"razorpay\"]'),('P-7YE21141N96098028NBPL2SY','Gold','Standard access to SalesWit features',12,'USD','year',1,'{\"requests\": 10}','saleswit','P-7YE21141N96098028NBPL2SY','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_Qowr90cfBbGmbk','international','[\"paypal\", \"razorpay\"]'),('P-9WD9503571007053SNBPEIHY','Platinum','Standard access to MarketFit features',22,'USD','month',1,'{\"document_pages\": 3000, \"perplexity_requests\": 20}','marketfit','P-9WD9503571007053SNBPEIHY','[\"paypal\", \"razorpay\"]',1,'2025-06-06 20:34:23','plan_QowpYMCuwPQrws','international','[\"paypal\", \"razorpay\"]'),('plan_free_marketfit','Free Plan','Basic access to MarketFit features',0,'INR','month',1,'{\"document_pages\": 15, \"perplexity_requests\": 2}','marketfit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:31:54',NULL,'domestic',NULL),('plan_free_saleswit','Free Plan','Basic access to SalesWit features',0,'INR','month',1,'{\"requests\": 3}','saleswit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:31:54',NULL,'domestic',NULL),('plan_Qf7Jas8ayXEcha','Gold','Standard access to MarketFit features',1000,'INR','year',1,'{\"document_pages\": 1000, \"perplexity_requests\": 10}','marketfit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_Qf7Jas8ayXEcha','domestic','[\"razorpay\"]'),('plan_Qm7TSBmLqQZnvp','Platinum','Standard access to MarketFit features',1500,'INR','year',1,'{\"document_pages\": 3000, \"perplexity_requests\": 20}','marketfit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_Qm7TSBmLqQZnvp','domestic','[\"razorpay\"]'),('plan_Qm7UsVIdJQWyNU','Gold','Standard access to MarketFit features',1250,'INR','month',1,'{\"document_pages\": 1000, \"perplexity_requests\": 10}','marketfit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_Qm7UsVIdJQWyNU','domestic','[\"razorpay\"]'),('plan_Qm7UW983v043Oj','Platinum','Standard access to MarketFit features',1830,'INR','month',1,'{\"document_pages\": 3000, \"perplexity_requests\": 20}','marketfit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_Qm7UW983v043Oj','domestic','[\"razorpay\"]'),('plan_QmHFZ8Ho9ikUH3','Gold','Standard access to SalesWit features',1000,'INR','year',1,'{\"requests\": 10}','saleswit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_QmHFZ8Ho9ikUH3','domestic','[\"razorpay\"]'),('plan_QmHGOEn5TwRA1V','Gold','Standard access to SalesWit features',1250,'INR','month',1,'{\"requests\": 10}','saleswit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_QmHGOEn5TwRA1V','domestic','[\"razorpay\"]'),('plan_QmHHUgzbNmRQDM','Platinum','Standard access to SalesWit features',1830,'INR','month',1,'{\"requests\": 20}','saleswit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_QmHHUgzbNmRQDM','domestic','[\"razorpay\"]'),('plan_QmHIBcQ9UaNcVH','Platinum','Standard access to SalesWit features',1500,'INR','year',1,'{\"requests\": 20}','saleswit',NULL,'[\"razorpay\"]',1,'2025-06-06 20:34:23','plan_QmHIBcQ9UaNcVH','domestic','[\"razorpay\"]');
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

-- Dump completed on 2025-07-04  3:27:34
