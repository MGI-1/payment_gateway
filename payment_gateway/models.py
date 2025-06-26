"""
Database models for payment gateway integration
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any, Union

@dataclass
class SubscriptionPlan:
    """Subscription plan model"""
    id: str
    name: str
    description: str
    amount: int
    currency: str
    interval: str
    interval_count: int
    features: Dict[str, Any]
    app_id: str
    plan_type: str = 'domestic'
    payment_gateways: List[str] = None
    paypal_plan_id: Optional[str] = None
    razorpay_plan_id: Optional[str] = None
    is_active: bool = True
    created_at: datetime = None
    
    @classmethod
    def from_db_dict(cls, data: Dict[str, Any]) -> 'SubscriptionPlan':
        """Create a SubscriptionPlan from a database dictionary"""
        from .utils.helpers import parse_json_field
        
        if not data:
            return None
            
        # Parse JSON fields
        features = parse_json_field(data.get('features'), {})
        payment_gateways = parse_json_field(data.get('payment_gateways'), ['razorpay'])
        
        return cls(
            id=data.get('id'),
            name=data.get('name'),
            description=data.get('description'),
            amount=data.get('amount', 0),
            currency=data.get('currency', 'INR'),
            interval=data.get('interval', 'month'),
            interval_count=data.get('interval_count', 1),
            features=features,
            app_id=data.get('app_id'),
            plan_type=data.get('plan_type', 'domestic'),
            payment_gateways=payment_gateways,
            paypal_plan_id=data.get('paypal_plan_id'),
            razorpay_plan_id=data.get('razorpay_plan_id'),
            is_active=data.get('is_active', True),
            created_at=data.get('created_at')
        )

@dataclass
class Subscription:
    """User subscription model"""
    id: str
    user_id: str
    plan_id: str
    status: str
    app_id: str
    razorpay_subscription_id: Optional[str] = None
    paypal_subscription_id: Optional[str] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    metadata: Dict[str, Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    plan_name: Optional[str] = None
    features: Dict[str, Any] = None
    amount: Optional[int] = None
    currency: Optional[str] = None
    interval: Optional[str] = None
    
    @property
    def is_active(self) -> bool:
        """Check if subscription is active"""
        return self.status == 'active'
    
    @property
    def is_cancelled(self) -> bool:
        """Check if subscription is cancelled"""
        return self.status == 'cancelled'
    
    @property
    def cancellation_scheduled(self) -> bool:
        """Check if cancellation is scheduled"""
        if not self.metadata:
            return False
        return self.metadata.get('cancellation_scheduled', False)
    
    @classmethod
    def from_db_dict(cls, data: Dict[str, Any]) -> 'Subscription':
        """Create a Subscription from a database dictionary"""
        from .utils.helpers import parse_json_field
        
        if not data:
            return None
            
        # Parse JSON fields
        metadata = parse_json_field(data.get('metadata'), {})
        features = parse_json_field(data.get('features'), {})
        
        return cls(
            id=data.get('id'),
            user_id=data.get('user_id'),
            plan_id=data.get('plan_id'),
            status=data.get('status'),
            app_id=data.get('app_id'),
            razorpay_subscription_id=data.get('razorpay_subscription_id'),
            paypal_subscription_id=data.get('paypal_subscription_id'),
            current_period_start=data.get('current_period_start'),
            current_period_end=data.get('current_period_end'),
            metadata=metadata,
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at'),
            plan_name=data.get('plan_name'),
            features=features,
            amount=data.get('amount'),
            currency=data.get('currency'),
            interval=data.get('interval')
        )

@dataclass
class Invoice:
    """Subscription invoice model"""
    id: str
    subscription_id: str
    user_id: str
    amount: int
    currency: str
    status: str
    app_id: str
    razorpay_invoice_id: Optional[str] = None
    paypal_invoice_id: Optional[str] = None
    payment_id: Optional[str] = None
    invoice_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    
    @classmethod
    def from_db_dict(cls, data: Dict[str, Any]) -> 'Invoice':
        """Create an Invoice from a database dictionary"""
        if not data:
            return None
            
        return cls(
            id=data.get('id'),
            subscription_id=data.get('subscription_id'),
            user_id=data.get('user_id'),
            amount=data.get('amount', 0),
            currency=data.get('currency', 'INR'),
            status=data.get('status'),
            app_id=data.get('app_id'),
            razorpay_invoice_id=data.get('razorpay_invoice_id'),
            paypal_invoice_id=data.get('paypal_invoice_id'),
            payment_id=data.get('payment_id'),
            invoice_date=data.get('invoice_date'),
            paid_at=data.get('paid_at'),
            created_at=data.get('created_at')
        )

@dataclass
class ResourceUsage:
    """Resource usage model"""
    id: Optional[int] = None
    user_id: str = None
    subscription_id: str = None
    app_id: str = None
    billing_period_start: Optional[datetime] = None
    billing_period_end: Optional[datetime] = None
    document_pages_count: int = 0
    perplexity_requests_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @classmethod
    def from_db_dict(cls, data: Dict[str, Any]) -> 'ResourceUsage':
        """Create a ResourceUsage from a database dictionary"""
        if not data:
            return None
            
        return cls(
            id=data.get('id'),
            user_id=data.get('user_id'),
            subscription_id=data.get('subscription_id'),
            app_id=data.get('app_id'),
            billing_period_start=data.get('billing_period_start'),
            billing_period_end=data.get('billing_period_end'),
            document_pages_count=data.get('document_pages_count', 0),
            perplexity_requests_count=data.get('perplexity_requests_count', 0),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at')
        )