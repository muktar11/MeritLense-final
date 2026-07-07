from rest_framework import serializers
from api.core.serializers import PublicIdModelSerializer
from api.core.public_ids import get_by_identifier
from .models import (
    Price, Customer, PaymentMethod, 
    Subscription, Payment, Invoice
)


class PriceSerializer(PublicIdModelSerializer):
    formatted_price = serializers.SerializerMethodField()
    target_user_type_display = serializers.SerializerMethodField()

    class Meta:
        model = Price
        fields = [
            'id', 'name', 'stripe_price_id', 'stripe_product_id',
            'target_user_type', 'target_user_type_display',
            'min_company_size', 'max_company_size',
            'unit_amount', 'currency', 'formatted_price',
            'interval', 'interval_count', 'billing_type',
            'evaluation_tier', 'task_observation_enabled',
            'features', 'feature_limits',
            'is_active', 'metadata', 'created_at'
        ]

    def get_formatted_price(self, obj):
        return obj.get_formatted_price()

    def get_target_user_type_display(self, obj):
        return dict(obj._meta.get_field('target_user_type').choices).get(obj.target_user_type, '')


class PriceAdminSerializer(PublicIdModelSerializer):
    """Admin-facing serializer for creating/editing packages (Price rows).

    stripe_price_id/stripe_product_id are intentionally read-only: they are
    always system-generated via StripeService, never typed in by an admin,
    so a package can't be wired to a mismatched/bogus Stripe object.
    """
    formatted_price = serializers.SerializerMethodField()

    class Meta:
        model = Price
        fields = [
            'id', 'name', 'stripe_price_id', 'stripe_product_id',
            'target_user_type', 'min_company_size', 'max_company_size',
            'unit_amount', 'currency', 'formatted_price',
            'billing_type', 'interval', 'interval_count',
            'evaluation_tier', 'task_observation_enabled',
            'features', 'feature_limits',
            'is_active', 'metadata', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'stripe_price_id', 'stripe_product_id', 'created_at', 'updated_at']

    def get_formatted_price(self, obj):
        return obj.get_formatted_price()

    def validate(self, data):
        billing_type = data.get('billing_type', getattr(self.instance, 'billing_type', 'RECURRING'))
        if billing_type == 'ONE_TIME' and data.get('interval'):
            # interval is meaningless for a one-time price; ignore rather than error,
            # so the same form can be reused for both billing types without extra client logic.
            data.pop('interval', None)
            data.pop('interval_count', None)
        return data

class CustomerSerializer(PublicIdModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Customer
        fields = [
            'id', 'user', 'user_email', 'user_name',
            'stripe_customer_id', 'email', 'name', 'phone',
            'default_payment_method_id', 'metadata',
            'created_at', 'updated_at'
        ]
    
    def get_user_name(self, obj):
        return obj.user.get_full_name()


class PaymentMethodSerializer(PublicIdModelSerializer):
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PaymentMethod
        fields = [
            'id', 'customer', 'stripe_payment_method_id',
            'method_type', 'display_name',
            'card_brand', 'card_last4', 'card_exp_month', 'card_exp_year',
            'is_default', 'is_active', 'billing_details',
            'created_at', 'updated_at'
        ]
    
    def get_display_name(self, obj):
        return str(obj)


class SubscriptionSerializer(PublicIdModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()
    price_details = PriceSerializer(source='stripe_price', read_only=True)
    is_active_subscription = serializers.SerializerMethodField()
    
    class Meta:
        model = Subscription
        fields = [
            'id', 'user', 'user_email', 'user_name',
            'company', 'customer', 'stripe_subscription_id',
            'stripe_price', 'price_details', 'status',
            'current_period_start', 'current_period_end',
            'trial_start', 'trial_end', 'canceled_at',
            'quantity', 'is_active_subscription',
            'metadata', 'created_at', 'updated_at'
        ]
    
    def get_user_name(self, obj):
        return obj.user.get_full_name()
    
    def get_is_active_subscription(self, obj):
        active_statuses = ['active', 'trialing', 'ACTIVE', 'TRIALING']
        return obj.status and obj.status.lower() in ['active', 'trialing']

class PaymentSerializer(PublicIdModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    payment_method_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Payment
        fields = [
            'id', 'user', 'user_email', 'company', 'customer',
            'subscription', 'stripe_payment_intent_id',
            'stripe_payment_method', 'payment_method_display',
            'amount', 'currency', 'status',
            'payment_method_type', 'receipt_url', 'receipt_number',
            'paid_at', 'refunded_at', 'metadata',
            'created_at', 'updated_at'
        ]
    
    def get_payment_method_display(self, obj):
        if obj.stripe_payment_method:
            return str(obj.stripe_payment_method)
        return obj.payment_method_type

class InvoiceSerializer(PublicIdModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'user', 'user_email', 'customer', 'subscription',
            'stripe_invoice_id', 'stripe_payment_intent',
            'number', 'status', 'amount_due', 'amount_paid',
            'amount_remaining', 'currency', 'due_date', 'paid_at',
            'voided_at', 'invoice_pdf', 'hosted_invoice_url',
            'metadata', 'created_at', 'updated_at'
        ]



class CreatePaymentIntentSerializer(serializers.Serializer):
    price_id = serializers.CharField(required=False)
    amount = serializers.DecimalField(
        max_digits=10, 
        decimal_places=2,
        required=False
    )
    currency = serializers.CharField(default='eur', max_length=3)
    payment_method_id = serializers.CharField(required=False)
    
    def validate(self, data):
        if not data.get('price_id') and not data.get('amount'):
            raise serializers.ValidationError(
                "Either price_id or amount must be provided"
            )
        return data


class CreateSetupIntentSerializer(serializers.Serializer):
    pass


class AttachPaymentMethodSerializer(serializers.Serializer):
    payment_method_id = serializers.CharField()
    set_default = serializers.BooleanField(default=False)



class CreateSubscriptionSerializer(serializers.Serializer):
    price_id = serializers.CharField()
    payment_method_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    trial_period_days = serializers.IntegerField(default=0, min_value=0)
    quantity = serializers.IntegerField(default=1, min_value=1)
    
    def validate_price_id(self, value):
        try:
            price = get_by_identifier(Price.objects.filter(is_active=True), value)
            self.context['price'] = price
            return value
        except Price.DoesNotExist:
            raise serializers.ValidationError("Price not found or inactive")
    
    def validate(self, data):
        request = self.context.get('request')
        user = request.user
        
        price = self.context.get('price')
        
        if price and not price.is_available_for_user(user):
            raise serializers.ValidationError(
                "This plan is not available for your account type"
            )
        
        if hasattr(user, 'company_profile') and user.company_profile:
            existing = Subscription.objects.filter(
                company=user.company_profile.company,
                status__in=['ACTIVE', 'TRIALING']
            ).exists()
        else:
            existing = Subscription.objects.filter(
                user=user,
                status__in=['ACTIVE', 'TRIALING']
            ).exists()
        
        if existing:
            raise serializers.ValidationError(
                "You already have an active subscription. Please cancel it first."
            )
        
        return data

class UpdateSubscriptionSerializer(serializers.Serializer):
    price_id = serializers.CharField(required=False)
    quantity = serializers.IntegerField(min_value=1, required=False)
    cancel_at_period_end = serializers.BooleanField(required=False)


class WebhookSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    data = serializers.JSONField()
