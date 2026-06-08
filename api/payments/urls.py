from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PriceViewSet, CustomerViewSet, PaymentMethodViewSet,
    SubscriptionViewSet, PaymentViewSet, InvoiceViewSet,
    StripeWebhookView, AdminSubscriptionViewSet, check_all_subscriptions, debug_subscription_data, debug_subscription_payment, retry_subscription_payment, sync_all_subscriptions, sync_subscription_status
)

router = DefaultRouter(trailing_slash=False)
router.register(r'prices', PriceViewSet, basename='price')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'payment-methods', PaymentMethodViewSet, basename='payment-method')
router.register(r'subscriptions', SubscriptionViewSet, basename='subscription')
router.register(r'payments', PaymentViewSet, basename='payment')
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'admin/subscriptions', AdminSubscriptionViewSet, basename='admin-subscription')

urlpatterns = [
    path('', include(router.urls)),
    path('webhook', StripeWebhookView.as_view({'post': 'post'}), name='stripe-webhook'),
]
urlpatterns += [
    path('sync-subscription', sync_subscription_status, name='sync-subscription'),
    path('check-subscriptions', check_all_subscriptions, name='check-subscriptions'),
    path('sync-all', sync_all_subscriptions, name='sync-all'),
    path('debug-payment', debug_subscription_payment, name='debug-payment'),
    path('retry-payment', retry_subscription_payment, name='retry-subscription-payment'),
    path('debug-subscription', debug_subscription_data, name='debug-subscription'),



]
