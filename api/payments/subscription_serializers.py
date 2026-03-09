from rest_framework import serializers
from django.utils import timezone
from .models import Subscription, Price


class PriceForSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Price
        fields = ['id', 'name', 'unit_amount', 'currency', 'interval', 'feature_limits']
        

class SubscriptionSerializer(serializers.ModelSerializer):
    plan_details = PriceForSubscriptionSerializer(source='stripe_price', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)
    payment_method_display = serializers.SerializerMethodField()
    usage_percentages = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()
    
    class Meta:
        model = Subscription
        fields = [
            'id', 'user', 'user_email', 'company', 'company_name',
            'customer', 'stripe_subscription_id',
            'stripe_price', 'plan_details',
            'status', 'status_display',
            'current_period_start', 'current_period_end',
            'trial_start', 'trial_end', 'canceled_at',
            'quantity', 'cancel_at_period_end',
            'default_payment_method', 'payment_method_display',
            'latest_invoice', 'current_usage', 'usage_percentages',
            'days_remaining', 'metadata',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'stripe_subscription_id']
    
    def get_payment_method_display(self, obj):
        if obj.default_payment_method:
            return str(obj.default_payment_method)
        return None
    
    def get_usage_percentages(self, obj):
        return obj.get_usage_percentage()
    
    def get_days_remaining(self, obj):
        if obj.current_period_end and obj.current_period_end > timezone.now():
            delta = obj.current_period_end - timezone.now()
            return max(0, delta.days)
        return 0
    
class SubscriptionListSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source='stripe_price.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    amount = serializers.DecimalField(source='stripe_price.unit_amount', max_digits=10, decimal_places=2, read_only=True)
    currency = serializers.CharField(source='stripe_price.currency', read_only=True)
    
    class Meta:
        model = Subscription
        fields = [
            'id', 'plan_name', 'status', 'status_display',
            'amount', 'currency', 'current_period_end',
            'cancel_at_period_end', 'quantity', 'created_at'
        ]
class ChangePlanSerializer(serializers.Serializer):
    price_id = serializers.IntegerField()
    prorate = serializers.BooleanField(default=True)
    
    def validate_price_id(self, value):
        try:
            price = Price.objects.get(id=value, is_active=True)
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
        
        return data

class UpdateQuantitySerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=999)
    
    def validate_quantity(self, value):
        return value

class SubscriptionInvoiceSerializer(serializers.Serializer):
    price_id = serializers.IntegerField(required=False)
    quantity = serializers.IntegerField(min_value=1, required=False)

class CancelSubscriptionSerializer(serializers.Serializer):
    at_period_end = serializers.BooleanField(default=True)
    cancellation_reason = serializers.CharField(required=False, allow_blank=True)


class ReactivateSubscriptionSerializer(serializers.Serializer):
    pass