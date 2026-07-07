from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
import stripe
from api.core.models import TimeStampedModel
from api.core.constants import (
    CompanySize, PaymentStatus, PaymentMethodConstants,
    SubscriptionStatus, BillingInterval, InvoiceStatus, InterviewEvaluationTier
)
from api.accounts.models import User, Company

class Price(TimeStampedModel):
    name = models.CharField(max_length=100)
    stripe_price_id = models.CharField(max_length=100, unique=True)
    stripe_product_id = models.CharField(max_length=100)
    
    target_user_type = models.CharField(
        max_length=10,
        choices=[
            ('B2C', 'Individual'),
            ('B2B', 'Company'),
            ('BOTH', 'Both')
        ],
        default='BOTH',
        help_text="Which user type this plan is available to"
    )
    
    min_company_size = models.CharField(
        max_length=10,
        choices=CompanySize.CHOICES,
        null=True,
        blank=True,
        help_text="Minimum company size required (for B2B plans)"
    )
    
    max_company_size = models.CharField(
        max_length=10,
        choices=CompanySize.CHOICES,
        null=True,
        blank=True,
        help_text="Maximum company size allowed (for B2B plans)"
    )
    
    unit_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        validators=[MinValueValidator(0)]
    )
    currency = models.CharField(max_length=3, default='usd')
    
    interval = models.CharField(
        max_length=10,
        choices=BillingInterval.CHOICES,
        default=BillingInterval.MONTHLY
    )
    interval_count = models.PositiveIntegerField(default=1)

    billing_type = models.CharField(
        max_length=10,
        choices=[
            ('RECURRING', 'Recurring'),
            ('ONE_TIME', 'One-Time'),
        ],
        default='RECURRING',
        help_text="Whether this package is a recurring subscription or a one-time purchase"
    )

    evaluation_tier = models.CharField(
        max_length=20,
        choices=InterviewEvaluationTier.CHOICES,
        null=True,
        blank=True,
        help_text="Evaluation tier this package grants (Screening/Full/Both)"
    )

    task_observation_enabled = models.BooleanField(
        default=False,
        help_text="Whether this package includes task observation sessions"
    )

    features = models.JSONField(default=dict, blank=True)
    
    feature_limits = models.JSONField(
        default=dict, 
        blank=True,
        help_text="Feature limits like candidate_limit, evaluation_limit, team_member_limit"
    )
    
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Price"
        verbose_name_plural = "Prices"
        ordering = ['target_user_type', 'unit_amount']
    
    def __str__(self):
        user_type_display = dict(self._meta.get_field('target_user_type').choices).get(self.target_user_type, '')
        return f"{self.name} ({user_type_display}) - {self.currency} {self.unit_amount}/{self.interval}"
    
    def get_formatted_price(self):
        return f"{self.currency} {self.unit_amount}"
    
    def is_available_for_user(self, user):
        if self.target_user_type == 'BOTH':
            return True
        
        if self.target_user_type == 'B2C' and user.role == 'B2C':
            return True
        
        if self.target_user_type == 'B2B' and user.role in ['B2B', 'B2B_TEAM']:
            if hasattr(user, 'company_profile') and self.min_company_size:
                company = user.company_profile.company
                return self._check_company_size(company)
            return True
        
        return False
    
    def _check_company_size(self, company):
        if not self.min_company_size and not self.max_company_size:
            return True
        
        size_order = ['1-10', '11-50', '51-200', '201-1000', '1000+']
        
        try:
            company_size_index = size_order.index(company.company_size)
            
            if self.min_company_size:
                min_index = size_order.index(self.min_company_size)
                if company_size_index < min_index:
                    return False
            
            if self.max_company_size:
                max_index = size_order.index(self.max_company_size)
                if company_size_index > max_index:
                    return False
            
            return True
        except (ValueError, AttributeError):
            return False

class Customer(TimeStampedModel):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='stripe_customer'
    )
    stripe_customer_id = models.CharField(max_length=100, unique=True)
    
    email = models.EmailField()
    name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    
    default_payment_method_id = models.CharField(
        max_length=100, 
        blank=True,
        help_text="Stripe payment method ID"
    )
    
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Customer"
        verbose_name_plural = "Customers"
        indexes = [
            models.Index(fields=['stripe_customer_id']),
            models.Index(fields=['user']),
        ]
    
    def __str__(self):
        return f"{self.email} - {self.stripe_customer_id}"


class PaymentMethod(TimeStampedModel):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='payment_methods'
    )
    stripe_payment_method_id = models.CharField(max_length=100, unique=True)
    
    method_type = models.CharField(
        max_length=20,
        choices=PaymentMethodConstants.CHOICES,
        default=PaymentMethodConstants.CARD
    )
    
    card_brand = models.CharField(max_length=50, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    card_exp_month = models.PositiveIntegerField(null=True, blank=True)
    card_exp_year = models.PositiveIntegerField(null=True, blank=True)
    
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    
    billing_details = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Payment Method"
        verbose_name_plural = "Payment Methods"
        ordering = ['-is_default', '-created_at']
    
    def __str__(self):
        if self.card_brand and self.card_last4:
            return f"{self.card_brand} •••• {self.card_last4}"
        return f"Payment Method {self.stripe_payment_method_id}"


class Subscription(TimeStampedModel):
    """
    Subscription model linked to Stripe
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='subscriptions'
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subscriptions'
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='subscriptions'
    )
    
    stripe_subscription_id = models.CharField(max_length=100, unique=True)
    stripe_price = models.ForeignKey(
        Price,
        on_delete=models.SET_NULL,
        null=True,
        related_name='subscriptions'
    )
    
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.CHOICES,
        default=SubscriptionStatus.INCOMPLETE
    )
    
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    trial_start = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    
    quantity = models.PositiveIntegerField(default=1)
    
    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text="If true, subscription will cancel at period end"
    )
    
    billing_cycle_anchor = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="The date when the subscription was created"
    )
    
    latest_invoice = models.ForeignKey(
        'Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+'
    )
    
    default_payment_method = models.ForeignKey(
        'PaymentMethod',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions'
    )
    
    current_usage = models.JSONField(
        default=dict,
        blank=True,
        help_text="Current usage counts for candidates, evaluations, team members"
    )
    
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"
        indexes = [
            models.Index(fields=['stripe_subscription_id']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['company', 'status']),
            models.Index(fields=['current_period_end']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        user_identifier = self.company.name if self.company else self.user.email
        return f"{user_identifier} - {self.status}"
    
    def save(self, *args, **kwargs):
        if not self.current_period_start:
            self.current_period_start = timezone.now()
        if not self.current_period_end:
            self.current_period_end = timezone.now() + timezone.timedelta(days=30)
        super().save(*args, **kwargs)
    
    def is_active(self):
        status_upper = self.status.upper() if self.status else ''
        return status_upper in [
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING
        ] or self.status in ['active', 'trialing']
    
    def cancel(self, at_period_end=True):
        try:
            import stripe
            from django.conf import settings
            stripe.api_key = settings.STRIPE_SECRET_KEY
            
            stripe_sub = stripe.Subscription.modify(
                self.stripe_subscription_id,
                cancel_at_period_end=at_period_end
            )
            
            self.cancel_at_period_end = at_period_end
            if not at_period_end:
                self.status = SubscriptionStatus.CANCELED
                self.canceled_at = timezone.now()
            
            self.save()
            return True
        except Exception as e:
            print(f"Error canceling subscription: {e}")
            return False
    
    def get_feature_limits(self):
        if self.stripe_price:
            return self.stripe_price.feature_limits
        return {}
    

    def get_usage_percentage(self):
        limits = self.get_feature_limits()
        usage = self.current_usage or {}
        percentages = {}
        
        for key, limit in limits.items():
            if limit and limit > 0:
                used = usage.get(key, 0)
                percentages[key] = {
                    'used': used,
                    'limit': limit,
                    'percentage': (used / limit) * 100 if limit > 0 else 0
                }
        
        return percentages

    def check_usage_limit(self, feature, increment=1):
        limits = self.get_feature_limits()
        if not limits or feature not in limits:
            return True
        
        limit = limits.get(feature, 0)
        if limit <= 0:
            return True
        
        current = self.current_usage.get(feature, 0)
        return (current + increment) <= limit

    def increment_usage(self, feature, increment=1):
        if self.check_usage_limit(feature, increment):
            current = self.current_usage.get(feature, 0)
            self.current_usage[feature] = current + increment
            self.save(update_fields=['current_usage'])
            return True
        return False
    
    def decrement_usage(self, feature, decrement=1):
        current = self.current_usage.get(feature, 0)
        self.current_usage[feature] = max(0, current - decrement)
        self.save(update_fields=['current_usage'])
        
class Payment(TimeStampedModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='payments'
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments'
    )
    
    stripe_payment_intent_id = models.CharField(
        max_length=100, 
        unique=True,
        db_index=True
    )
    stripe_payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        validators=[MinValueValidator(0)]
    )
    currency = models.CharField(max_length=3, default='eur')
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.CHOICES,
        default=PaymentStatus.PENDING
    )
    
    payment_method_type = models.CharField(
        max_length=20,
        choices=PaymentMethodConstants.CHOICES,
        default=PaymentMethodConstants.CARD
    )
    
    receipt_url = models.URLField(blank=True)
    receipt_number = models.CharField(max_length=100, blank=True)
    
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        indexes = [
            models.Index(fields=['stripe_payment_intent_id']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['created_at']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Payment {self.stripe_payment_intent_id} - {self.amount} {self.currency}"

class Invoice(TimeStampedModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='invoices'
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='invoices'
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices'
    )
    
    stripe_invoice_id = models.CharField(max_length=100, unique=True)
    stripe_payment_intent = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices'
    )
    
    number = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.CHOICES,
        default=InvoiceStatus.DRAFT
    )
    
    amount_due = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    amount_remaining = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='eur')
    
    due_date = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    
    invoice_pdf = models.URLField(blank=True)
    
    metadata = models.JSONField(default=dict, blank=True)
    hosted_invoice_url = models.URLField(blank=True)
    
    class Meta:
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
        indexes = [
            models.Index(fields=['stripe_invoice_id']),
            models.Index(fields=['user', 'status']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Invoice {self.number} - {self.status}"