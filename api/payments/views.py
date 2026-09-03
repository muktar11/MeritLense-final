from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db.models import Q
from django.utils import timezone
import stripe
from api.accounts.models import User
from api.core.constants import Roles, SubscriptionStatus
from api.core.permisssions import IsAdminOrSuperAdmin, IsSuperAdmin
from api.payments.subscription_serializers import CancelSubscriptionSerializer, ChangePlanSerializer, SubscriptionListSerializer, UpdateQuantitySerializer
from meritlense import settings
from api.audit.services import AuditLogService
from api.core.constants import AuditLogCategory, AuditLogAction, AuditLogSeverity
from api.core.public_ids import PublicIdLookupMixin, get_by_identifier

from .models import Price, Customer, PaymentMethod, Subscription, Payment, Invoice, ProcessedStripeEvent
from .serializers import (
    PriceSerializer, PriceAdminSerializer, CustomerSerializer, PaymentMethodSerializer,
    SubscriptionSerializer, PaymentSerializer, InvoiceSerializer,
    CreatePaymentIntentSerializer, AttachPaymentMethodSerializer, CreateSubscriptionSerializer,
    RefundPaymentSerializer
)
from .services import StripeService


class PriceViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PriceSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Price.objects.none()

        queryset = Price.objects.filter(is_active=True)
        user = self.request.user
        
        if user.role == 'B2C':
            queryset = queryset.filter(
                Q(target_user_type='B2C') | Q(target_user_type='BOTH')
            )
        elif user.role in ['B2B', 'B2B_TEAM_MEMBER']:
            queryset = queryset.filter(
                Q(target_user_type='B2B') | Q(target_user_type='BOTH')
            )
        
        if user.role in ['B2B', 'B2B_TEAM_MEMBER'] and hasattr(user, 'company_profile'):
            allowed_ids = [
                price.id for price in queryset
                if price.is_available_for_user(user)
            ]
            queryset = queryset.filter(id__in=allowed_ids)
        
        return queryset
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_PRICES_LIST',
            category=AuditLogCategory.SUBSCRIPTION,
            description="Price/plan list viewed",
            data={'count': len(response.data) if hasattr(response, 'data') else 0},
            request=request
        )
        
        return response
    
    @action(detail=False, methods=['get'])
    def for_user(self, request):
        queryset = self.get_queryset()
        
        interval = request.query_params.get('interval')
        if interval:
            queryset = queryset.filter(interval=interval)
        
        serializer = self.get_serializer(queryset, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_PRICES_FOR_USER',
            category=AuditLogCategory.SUBSCRIPTION,
            description="Filtered prices viewed",
            data={'interval': interval, 'count': len(serializer.data)},
            request=request
        )
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def sync_from_stripe(self, request):
        try:
            import stripe
            from django.conf import settings
            stripe.api_key = settings.STRIPE_SECRET_KEY
            
            stripe_prices = stripe.Price.list(limit=100, active=True)
            
            created_count = 0
            updated_count = 0
            synced_prices = []
            
            for stripe_price in stripe_prices.data:
                product = stripe.Product.retrieve(stripe_price.product)
                
                user_type = product.metadata.get('user_type', 'BOTH')
                min_size = product.metadata.get('min_company_size')
                max_size = product.metadata.get('max_company_size')
                
                features = {}
                feature_limits = {}
                
                if product.metadata.get('candidate_limit'):
                    feature_limits['candidate_limit'] = int(product.metadata['candidate_limit'])
                
                if product.metadata.get('evaluation_limit'):
                    feature_limits['evaluation_limit'] = int(product.metadata['evaluation_limit'])
                
                if product.metadata.get('team_member_limit'):
                    feature_limits['team_member_limit'] = int(product.metadata['team_member_limit'])
                
                price, created = Price.objects.update_or_create(
                    stripe_price_id=stripe_price.id,
                    defaults={
                        'name': product.name,
                        'stripe_product_id': stripe_price.product,
                        'target_user_type': user_type,
                        'min_company_size': min_size if min_size != 'None' else None,
                        'max_company_size': max_size if max_size != 'None' else None,
                        'unit_amount': stripe_price.unit_amount / 100,
                        'currency': stripe_price.currency,
                        'interval': stripe_price.recurring['interval'].upper() if stripe_price.recurring else None,
                        'interval_count': stripe_price.recurring.get('interval_count', 1) if stripe_price.recurring else None,
                        'features': features,
                        'feature_limits': feature_limits,
                        'is_active': stripe_price.active,
                        'metadata': stripe_price.metadata
                    }
                )
                
                if created:
                    created_count += 1
                    synced_prices.append({'name': product.name, 'stripe_price_id': stripe_price.id})
                else:
                    updated_count += 1
            
            AuditLogService.log(
                user=request.user,
                action='SYNC_PRICES_FROM_STRIPE',
                category=AuditLogCategory.SUBSCRIPTION,
                description=f"Synced prices from Stripe: {created_count} created, {updated_count} updated",
                data={
                    'created_count': created_count,
                    'updated_count': updated_count,
                    'total': len(stripe_prices.data),
                    'synced_prices': synced_prices
                },
                request=request,
                severity=AuditLogSeverity.INFO
            )
            
            return Response({
                'message': f'Synced {created_count} new prices, updated {updated_count} existing prices',
                'total': len(stripe_prices.data)
            })
        except Exception as e:
            AuditLogService.log(
                user=request.user,
                action='SYNC_PRICES_FROM_STRIPE',
                category=AuditLogCategory.SUBSCRIPTION,
                description=f"Failed to sync prices from Stripe: {str(e)}",
                data={'error': str(e)},
                request=request,
                severity=AuditLogSeverity.ERROR
            )
            
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class CustomerViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CustomerSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Customer.objects.none()
        return Customer.objects.filter(user=self.request.user)
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        service = StripeService()
        customer = service.get_or_create_customer(request.user)
        
        if customer:
            serializer = self.get_serializer(customer)
            
            AuditLogService.log(
                user=request.user,
                action='CUSTOMER_RETRIEVED',
                category=AuditLogCategory.BILLING,
                description=f"Stripe customer retrieved: {customer.stripe_customer_id}",
                resource=customer,
                data={'stripe_customer_id': customer.stripe_customer_id},
                request=request
            )
            
            return Response(serializer.data)
        
        return Response(
            {'error': 'Could not create customer'},
            status=status.HTTP_400_BAD_REQUEST
        )


class PaymentMethodViewSet(PublicIdLookupMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PaymentMethodSerializer
    queryset = PaymentMethod.objects.none()
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PaymentMethod.objects.none()
        return PaymentMethod.objects.filter(
            customer__user=self.request.user,
            is_active=True
        )
    
    def list(self, request):
        service = StripeService()
        payment_methods = service.list_payment_methods(request.user)
        serializer = self.get_serializer(payment_methods, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_PAYMENT_METHODS',
            category=AuditLogCategory.BILLING,
            description="Payment methods listed",
            data={'count': len(payment_methods)},
            request=request
        )
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def create_setup_intent(self, request):
        service = StripeService()
        result = service.create_setup_intent(request.user)
        
        if result:
            AuditLogService.log(
                user=request.user,
                action='CREATE_SETUP_INTENT',
                category=AuditLogCategory.BILLING,
                description="Setup intent created for adding payment method",
                data={'setup_intent_id': result['setup_intent_id']},
                request=request
            )
            
            return Response(result)
        
        return Response(
            {'error': 'Could not create setup intent'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    @action(detail=False, methods=['post'])
    def attach(self, request):
        serializer = AttachPaymentMethodSerializer(data=request.data)
        if serializer.is_valid():
            service = StripeService()
            payment_method = service.attach_payment_method(
                request.user,
                serializer.validated_data['payment_method_id'],
                serializer.validated_data['set_default']
            )
            
            if payment_method:
                AuditLogService.log(
                    user=request.user,
                    action='ATTACH_PAYMENT_METHOD',
                    category=AuditLogCategory.BILLING,
                    description=f"Payment method attached: {payment_method.card_brand} ending in {payment_method.card_last4}",
                    resource=payment_method,
                    data={
                        'payment_method_id': payment_method.stripe_payment_method_id,
                        'card_brand': payment_method.card_brand,
                        'card_last4': payment_method.card_last4,
                        'is_default': payment_method.is_default
                    },
                    request=request
                )
                
                return Response(
                    PaymentMethodSerializer(payment_method).data,
                    status=status.HTTP_201_CREATED
                )
            
            return Response(
                {'error': 'Could not attach payment method'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def destroy(self, request, id=None):
        payment_method = self.get_object()
        
        method_info = {
            'id': str(payment_method.public_id),
            'stripe_payment_method_id': payment_method.stripe_payment_method_id,
            'card_brand': payment_method.card_brand,
            'card_last4': payment_method.card_last4,
            'was_default': payment_method.is_default
        }
        
        service = StripeService()
        success = service.detach_payment_method(
            payment_method.stripe_payment_method_id
        )
        
        if success:
            AuditLogService.log(
                user=request.user,
                action='DETACH_PAYMENT_METHOD',
                category=AuditLogCategory.BILLING,
                description=f"Payment method detached: {payment_method.card_brand} ending in {payment_method.card_last4}",
                data=method_info,
                request=request,
                severity=AuditLogSeverity.WARNING
            )
            
            return Response(status=status.HTTP_204_NO_CONTENT)
        
        return Response(
            {'error': 'Could not detach payment method'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    @action(detail=True, methods=['post'])
    def set_default(self, request, id=None):
        payment_method = self.get_object()
        
        service = StripeService()
        customer = service.get_or_create_customer(request.user)
        
        stripe.Customer.modify(
            customer.stripe_customer_id,
            invoice_settings={
                'default_payment_method': payment_method.stripe_payment_method_id
            }
        )
        
        PaymentMethod.objects.filter(customer=customer).update(is_default=False)
        payment_method.is_default = True
        payment_method.save()
        
        customer.default_payment_method_id = payment_method.stripe_payment_method_id
        customer.save()
        
        AuditLogService.log(
            user=request.user,
            action='SET_DEFAULT_PAYMENT_METHOD',
            category=AuditLogCategory.BILLING,
            description=f"Default payment method changed to: {payment_method.card_brand} ending in {payment_method.card_last4}",
            resource=payment_method,
            data={
                'payment_method_id': str(payment_method.public_id),
                'card_brand': payment_method.card_brand,
                'card_last4': payment_method.card_last4
            },
            request=request
        )
        
        return Response({'message': 'Default payment method updated'})


class SubscriptionViewSet(PublicIdLookupMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Subscription.objects.none()
    
    def get_serializer_class(self):
        if self.action == 'list':
            return SubscriptionListSerializer
        return SubscriptionSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Subscription.objects.none()

        user = self.request.user
        
        if user.role in [Roles.SUPERADMIN, Roles.ADMIN]:
            return Subscription.objects.all()
        
        if user.role == Roles.B2C:
            return Subscription.objects.filter(user=user)
        
        if user.role == Roles.B2B and hasattr(user, 'company_profile'):
            return Subscription.objects.filter(company=user.company_profile.company)
        
        if user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, 'team_member_profile'):
            company = user.team_member_profile.company
            return Subscription.objects.filter(company=company)
        
        return Subscription.objects.none()
    
    def list(self, request):
        subscriptions = self.get_queryset()
        
        status_filter = request.query_params.get('status')
        if status_filter:
            subscriptions = subscriptions.filter(status=status_filter)
        
        active_only = request.query_params.get('active_only')
        if active_only and active_only.lower() == 'true':
            subscriptions = subscriptions.filter(
                status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
            )
        
        serializer = self.get_serializer(subscriptions, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_SUBSCRIPTIONS_LIST',
            category=AuditLogCategory.SUBSCRIPTION,
            description="Subscriptions list viewed",
            data={'count': len(serializer.data)},
            request=request
        )
        
        return Response(serializer.data)
    
    def retrieve(self, request, id=None):
        subscription = self.get_object()
        serializer = self.get_serializer(subscription)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_SUBSCRIPTION_DETAILS',
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Subscription details viewed: {subscription.stripe_subscription_id}",
            resource=subscription,
            data={
                'plan': subscription.stripe_price.name if subscription.stripe_price else None,
                'status': subscription.status
            },
            request=request
        )
        
        return Response(serializer.data)
    
    def create(self, request):
        logger.info(f"Creating subscription for user {request.user.id}")
        logger.info(f"Request data: {request.data}")
        
        serializer = CreateSubscriptionSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            try:
                service = StripeService()
                subscription = service.create_subscription(
                    user=request.user,
                    price_id=serializer.validated_data['price_id'],
                    payment_method_id=serializer.validated_data.get('payment_method_id'),
                    trial_period_days=serializer.validated_data.get('trial_period_days', 0),
                    quantity=serializer.validated_data.get('quantity', 1)
                )
                
                if subscription:
                    logger.info(f"Subscription created successfully: {subscription.id}")
                    
                    AuditLogService.log(
                        user=request.user,
                        action=AuditLogAction.SUBSCRIPTION_CREATED,
                        category=AuditLogCategory.SUBSCRIPTION,
                        description=f"Subscription created: {subscription.stripe_price.name if subscription.stripe_price else 'Unknown'}",
                        resource=subscription,
                        data={
                            'price_id': serializer.validated_data['price_id'],
                            'price_name': subscription.stripe_price.name if subscription.stripe_price else None,
                            'quantity': serializer.validated_data.get('quantity', 1),
                            'trial_days': serializer.validated_data.get('trial_period_days', 0)
                        },
                        request=request
                    )
                    
                    return Response(
                        SubscriptionSerializer(subscription).data,
                        status=status.HTTP_201_CREATED
                    )
                else:
                    logger.error("Failed to create subscription - service returned None")
                    return Response(
                        {'error': 'Failed to create subscription. Please check logs.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except stripe.error.StripeError as e:
                logger.error(f"Stripe error: {str(e)}")
                
                AuditLogService.log(
                    user=request.user,
                    action='SUBSCRIPTION_CREATION_FAILED',
                    category=AuditLogCategory.SUBSCRIPTION,
                    description=f"Subscription creation failed: {str(e)}",
                    data={'error': str(e)},
                    request=request,
                    severity=AuditLogSeverity.ERROR
                )
                
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                return Response(
                    {'error': 'An unexpected error occurred'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        logger.error(f"Serializer errors: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def change_plan(self, request, id=None):
        subscription = self.get_object()
        old_price = subscription.stripe_price
        old_plan = old_price.name if old_price else 'Unknown'

        serializer = ChangePlanSerializer(
            data=request.data,
            context={'request': request}
        )

        if serializer.is_valid():
            price_id = serializer.validated_data['price_id']
            prorate = serializer.validated_data['prorate']

            try:
                new_price = get_by_identifier(Price.objects.filter(is_active=True), price_id)

                stripe.api_key = settings.STRIPE_SECRET_KEY

                # Stripe's items[].id must be the Subscription Item ID (si_...),
                # not the Price ID — it identifies which line item to swap.
                stripe_subscription = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
                subscription_item_id = stripe_subscription['items']['data'][0]['id']

                items = [{
                    'id': subscription_item_id,
                    'price': new_price.stripe_price_id,
                }]

                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    items=items,
                    proration_behavior='create_prorations' if prorate else 'none'
                )

                subscription.stripe_price = new_price
                subscription.save()
                subscription.refresh_from_db()

                # Per the Package Architecture memo (Section 5): "Creating an
                # invoice, Checkout Session, or Stripe Subscription does not
                # by itself grant or renew Points or Slots. Entitlements are
                # activated only according to the confirmed-payment rules of
                # the applicable billing model." The subscription-item swap
                # above only takes effect on Stripe's side - by itself it
                # never touched PackageBalance, so an upgrade silently gave
                # the new plan's price with none of its entitlement until
                # the next unrelated monthly renewal.
                #
                # Upgrade (strictly more expensive, and actually prorated -
                # otherwise there's no new payment to confirm): invoice and
                # collect the proration immediately, and on confirmed
                # payment, grant the new plan's entitlement right away via
                # EntitlementService.apply_upgrade_grant() - per the
                # Product/Business Decision Sign-Off (Section 3), this adds
                # the new plan's full entitlement on top of any unused
                # balance (not a reset/overwrite - that's reset_b2b_balances,
                # reserved for normal renewal), capped at one full grant per
                # billing cycle. Calling it here is safe even if the eventual
                # invoice.paid webhook also fires for the same invoice - both
                # the Invoice upsert and apply_upgrade_grant are idempotent.
                #
                # Downgrade (or an upgrade with prorate=False, so nothing
                # new was actually charged): per the memo's explicit
                # instruction, "No automatic entitlement reduction should
                # occur without an approved package-change rule" - leave
                # the current balance untouched. It naturally rolls over to
                # the new (lower) plan's amount at the next regular renewal,
                # which is the "next billing cycle" effective date the memo
                # lists as one of the allowed options.
                is_upsized = prorate and old_price is not None and new_price.unit_amount > old_price.unit_amount
                if is_upsized and subscription.company:
                    try:
                        pending_invoice = stripe.Invoice.create(
                            customer=subscription.customer.stripe_customer_id,
                            subscription=subscription.stripe_subscription_id,
                            auto_advance=False,
                        )
                        pending_invoice = pending_invoice.finalize_invoice()
                        paid_invoice = pending_invoice.pay()
                        if paid_invoice.status == 'paid':
                            from .entitlement_services import EntitlementService
                            EntitlementService.apply_upgrade_grant(subscription)
                    except stripe.error.StripeError as e:
                        # The plan change on Stripe's side already succeeded
                        # (matches Stripe's own behavior: a failed proration
                        # invoice doesn't revert the subscription price) -
                        # entitlements simply stay at the old amount until
                        # payment is actually confirmed, via Stripe's normal
                        # invoice retry/dunning firing invoice.paid later.
                        logger.warning(f"Could not immediately invoice upgrade proration for subscription {subscription.id}: {e}")

                AuditLogService.log(
                    user=request.user,
                    action='SUBSCRIPTION_PLAN_CHANGED',
                    category=AuditLogCategory.SUBSCRIPTION,
                    description=f"Subscription plan changed from {old_plan} to {new_price.name}",
                    resource=subscription,
                    data={
                        'old_plan': old_plan,
                        'new_plan': new_price.name,
                        'prorate': prorate
                    },
                    request=request
                )

                return Response(SubscriptionSerializer(subscription).data)

            except stripe.error.StripeError as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Price.DoesNotExist:
                return Response(
                    {'error': 'New price not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def update_quantity(self, request, id=None):
        subscription = self.get_object()
        old_quantity = subscription.quantity
        
        serializer = UpdateQuantitySerializer(data=request.data)
        
        if serializer.is_valid():
            new_quantity = serializer.validated_data['quantity']
            
            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    items=[{
                        'id': subscription.stripe_price.stripe_price_id,
                        'quantity': new_quantity
                    }]
                )
                
                subscription.quantity = new_quantity
                subscription.save()
                
                AuditLogService.log(
                    user=request.user,
                    action='SUBSCRIPTION_QUANTITY_UPDATED',
                    category=AuditLogCategory.SUBSCRIPTION,
                    description=f"Subscription quantity updated from {old_quantity} to {new_quantity}",
                    resource=subscription,
                    data={
                        'old_quantity': old_quantity,
                        'new_quantity': new_quantity
                    },
                    request=request
                )
                
                return Response({
                    'message': 'Quantity updated successfully',
                    'quantity': new_quantity
                })
                
            except stripe.error.StripeError as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, id=None):
        subscription = self.get_object()
        serializer = CancelSubscriptionSerializer(data=request.data)
        
        if serializer.is_valid():
            at_period_end = serializer.validated_data['at_period_end']
            reason = serializer.validated_data.get('cancellation_reason', '')
            
            if reason:
                subscription.metadata['cancellation_reason'] = reason
                subscription.save()
            
            success = subscription.cancel(at_period_end)
            
            if success:
                subscription.refresh_from_db()
                
                AuditLogService.log(
                    user=request.user,
                    action=AuditLogAction.SUBSCRIPTION_CANCELLED,
                    category=AuditLogCategory.SUBSCRIPTION,
                    description=f"Subscription cancelled for {subscription.user.email}",
                    resource=subscription,
                    data={
                        'at_period_end': at_period_end,
                        'reason': reason
                    },
                    request=request,
                    severity=AuditLogSeverity.WARNING
                )
                
                return Response({
                    'message': 'Subscription cancelled successfully',
                    'subscription': SubscriptionSerializer(subscription).data
                })
            return Response(
                {'error': 'Failed to cancel subscription'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def reactivate(self, request, id=None):
        subscription = self.get_object()
        
        if subscription.status != SubscriptionStatus.CANCELED:
            return Response(
                {'error': 'Only cancelled subscriptions can be reactivated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            
            stripe.Subscription.modify(
                subscription.stripe_subscription_id,
                cancel_at_period_end=False
            )
            
            old_status = subscription.status
            subscription.cancel_at_period_end = False
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.save()
            
            AuditLogService.log(
                user=request.user,
                action='SUBSCRIPTION_REACTIVATED',
                category=AuditLogCategory.SUBSCRIPTION,
                description=f"Subscription reactivated for {subscription.user.email}",
                resource=subscription,
                data={'old_status': old_status, 'new_status': subscription.status},
                request=request
            )
            
            return Response({
                'message': 'Subscription reactivated successfully',
                'subscription': SubscriptionSerializer(subscription).data
            })
            
        except stripe.error.StripeError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['get'])
    def upcoming_invoice(self, request, id=None):
        subscription = self.get_object()
        price_id = request.query_params.get('price_id')
        quantity = request.query_params.get('quantity')
        
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            
            items = [{
                'id': subscription.stripe_price.stripe_price_id,
                'price': subscription.stripe_price.stripe_price_id,
                'quantity': subscription.quantity
            }]
            
            preview_changes = {}
            if price_id:
                try:
                    new_price = get_by_identifier(Price.objects.filter(is_active=True), price_id)
                    items[0]['price'] = new_price.stripe_price_id
                    preview_changes['price'] = {'old': subscription.stripe_price.name, 'new': new_price.name}
                except Price.DoesNotExist:
                    pass
            
            if quantity:
                items[0]['quantity'] = int(quantity)
                preview_changes['quantity'] = {'old': subscription.quantity, 'new': int(quantity)}
            
            invoice = stripe.Invoice.upcoming(
                subscription=subscription.stripe_subscription_id,
                subscription_items=items
            )
            
            AuditLogService.log(
                user=request.user,
                action='VIEW_UPCOMING_INVOICE',
                category=AuditLogCategory.BILLING,
                description="Upcoming invoice preview viewed",
                resource=subscription,
                data={
                    'amount_due': invoice.amount_due / 100,
                    'preview_changes': preview_changes if preview_changes else None
                },
                request=request
            )
            
            return Response({
                'amount_due': invoice.amount_due / 100,
                'amount_paid': invoice.amount_paid / 100,
                'amount_remaining': invoice.amount_remaining / 100,
                'currency': invoice.currency,
                'next_payment_attempt': invoice.next_payment_attempt,
                'lines': [
                    {
                        'description': line.description,
                        'amount': line.amount / 100,
                        'period': {
                            'start': line.period.start,
                            'end': line.period.end
                        }
                    }
                    for line in invoice.lines.data
                ]
            })
        except stripe.error.StripeError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['get'])
    def usage(self, request, id=None):
        subscription = self.get_object()
        
        usage_data = {
            'current_usage': subscription.current_usage,
            'usage_percentages': subscription.get_usage_percentage(),
            'limits': subscription.get_feature_limits()
        }
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_SUBSCRIPTION_USAGE',
            category=AuditLogCategory.SUBSCRIPTION,
            description="Subscription usage viewed",
            resource=subscription,
            data={'usage_data': subscription.current_usage},
            request=request
        )
        
        return Response(usage_data)
    
    @action(detail=False, methods=['get'])
    def active(self, request):
        user = request.user
        
        queryset = self.get_queryset().filter(
            status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
        )
        
        serializer = SubscriptionListSerializer(queryset, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_ACTIVE_SUBSCRIPTIONS',
            category=AuditLogCategory.SUBSCRIPTION,
            description="Active subscriptions viewed",
            data={'count': len(serializer.data)},
            request=request
        )
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def invoices(self, request):
        subscriptions = self.get_queryset()
        invoices = Invoice.objects.filter(
            subscription__in=subscriptions
        ).order_by('-created_at')
        
        from .serializers import InvoiceSerializer
        serializer = InvoiceSerializer(invoices, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_SUBSCRIPTION_INVOICES',
            category=AuditLogCategory.BILLING,
            description="Subscription invoices viewed",
            data={'count': len(serializer.data)},
            request=request
        )
        
        return Response(serializer.data)


class AdminPricePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AdminPriceViewSet(PublicIdLookupMixin, viewsets.ModelViewSet):
    """SuperAdmin-only CRUD over packages (Price rows).

    Distinct from the read-only, role-filtered PriceViewSet above, which
    regular authenticated users hit to see the packages available to them.
    This viewset is the only write path for packages and always keeps
    every Price row (including deactivated ones) visible to the SuperAdmin.
    """
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = PriceAdminSerializer
    pagination_class = AdminPricePagination

    # Fields that Stripe treats as immutable on a Price object — changing
    # any of these requires retiring the old Stripe Price and minting a new one.
    IMMUTABLE_PRICE_FIELDS = {'unit_amount', 'currency', 'billing_type', 'interval', 'interval_count'}

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Price.objects.none()

        queryset = Price.objects.all()

        target_user_type = self.request.query_params.get('target_user_type')
        if target_user_type:
            queryset = queryset.filter(target_user_type=target_user_type)

        billing_type = self.request.query_params.get('billing_type')
        if billing_type:
            queryset = queryset.filter(billing_type=billing_type)

        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        return queryset.order_by('target_user_type', 'unit_amount')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            stripe_price, product = StripeService().create_price_and_product(data)
        except stripe.error.StripeError as e:
            return Response({'error': f'Stripe error: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

        price = Price.objects.create(
            stripe_price_id=stripe_price.id,
            stripe_product_id=product.id,
            **data
        )

        AuditLogService.log(
            user=request.user,
            action='PACKAGE_CREATED',
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package created: {price.name}",
            resource=price,
            data={'name': price.name, 'target_user_type': price.target_user_type},
            request=request
        )

        return Response(self.get_serializer(price).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        pricing_changed = any(field in data for field in self.IMMUTABLE_PRICE_FIELDS)

        try:
            service = StripeService()
            if pricing_changed:
                new_price = service.retire_and_replace_price(instance, data)
                result = new_price
            else:
                service.update_price_metadata(instance, data)
                for field, value in data.items():
                    setattr(instance, field, value)
                instance.save()
                result = instance
        except stripe.error.StripeError as e:
            return Response({'error': f'Stripe error: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

        AuditLogService.log(
            user=request.user,
            action='PACKAGE_UPDATED',
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package updated: {result.name}",
            resource=result,
            data={'changes': list(data.keys()), 'pricing_changed': pricing_changed},
            request=request
        )

        return Response(self.get_serializer(result).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        # Permanent delete is only offered for packages nobody was ever
        # subscribed to - Subscription.stripe_price is SET_NULL on delete,
        # so it wouldn't crash on a package with real history, but it
        # would silently erase which plan those subscriptions were on.
        # Deactivation (the default) is the only safe path once a package
        # has ever actually had a subscriber.
        permanent = request.query_params.get('permanent', '').lower() == 'true'
        if permanent:
            if instance.subscriptions.exists():
                return Response(
                    {'error': 'This package has subscription history and cannot be permanently deleted - deactivate it instead.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                stripe.Price.modify(instance.stripe_price_id, active=False)
                stripe.Product.modify(instance.stripe_product_id, active=False)
            except stripe.error.StripeError as e:
                logger.warning(f"Could not archive Stripe price/product for {instance.stripe_price_id}: {e}")

            AuditLogService.log(
                user=request.user,
                action='PACKAGE_DELETED',
                category=AuditLogCategory.SUBSCRIPTION,
                description=f"Package permanently deleted: {instance.name}",
                resource=instance,
                request=request
            )
            instance.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            stripe.Price.modify(instance.stripe_price_id, active=False)
        except stripe.error.StripeError as e:
            logger.warning(f"Could not archive Stripe price {instance.stripe_price_id}: {e}")

        instance.is_active = False
        instance.save(update_fields=['is_active'])

        AuditLogService.log(
            user=request.user,
            action='PACKAGE_DEACTIVATED',
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package deactivated: {instance.name}",
            resource=instance,
            request=request
        )

        return Response(self.get_serializer(instance).data, status=status.HTTP_200_OK)


class AdminPaymentViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    """Admin/SuperAdmin-only visibility over ALL payments (unlike the
    user-facing PaymentViewSet above, which is scoped to request.user's own
    payments) - the manual refund action lives here."""
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = PaymentSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Payment.objects.none()
        return Payment.objects.all().order_by('-created_at')

    @action(detail=True, methods=['post'])
    def refund(self, request, id=None):
        payment = self.get_object()
        serializer = RefundPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            from .refund_services import RefundService
            RefundService.refund_payment(
                payment=payment, actor=request.user,
                override_reason_code=serializer.validated_data.get('override_reason_code'),
            )
        except (stripe.error.StripeError, ValueError) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PaymentSerializer(payment).data)


class AdminSubscriptionPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class AdminSubscriptionViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = SubscriptionSerializer
    pagination_class = AdminSubscriptionPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Subscription.objects.none()

        queryset = Subscription.objects.select_related('user', 'company', 'stripe_price').all()

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status__iexact=status_filter)

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search) |
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search) |
                Q(company__name__icontains=search)
            )

        return queryset.order_by('-created_at')
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)

        if hasattr(response, 'data') and isinstance(response.data, dict):
            result_count = response.data.get('count', 0)
        elif hasattr(response, 'data'):
            result_count = len(response.data)
        else:
            result_count = 0

        AuditLogService.log(
            user=request.user,
            action='ADMIN_VIEW_ALL_SUBSCRIPTIONS',
            category=AuditLogCategory.SUBSCRIPTION,
            description="Admin viewed all subscriptions",
            data={'count': result_count},
            request=request
        )

        return response
    
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        
        subscription = self.get_object()
        AuditLogService.log(
            user=request.user,
            action='ADMIN_VIEW_SUBSCRIPTION_DETAILS',
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Admin viewed subscription details for {subscription.user.email}",
            resource=subscription,
            request=request
        )
        
        return response
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        total_subscriptions = Subscription.objects.count()
        
        active_subscriptions = Subscription.objects.filter(
            Q(status__iexact='active') | Q(status__iexact='trialing')
        ).count()
        
        active_subs = Subscription.objects.filter(
            Q(status__iexact='active') | Q(status__iexact='trialing')
        ).select_related('stripe_price')
        
        monthly_revenue = 0
        by_plan = {}
        plan_details = []
        
        for sub in active_subs:
            if sub.stripe_price and sub.stripe_price.currency.lower() == 'eur':
                plan_name = sub.stripe_price.name if sub.stripe_price else 'Unknown'
                
                by_plan[plan_name] = by_plan.get(plan_name, 0) + 1
                
                if sub.stripe_price.interval in ['MONTH', 'MONTHLY']:
                    revenue = float(sub.stripe_price.unit_amount) * sub.quantity
                elif sub.stripe_price.interval in ['YEAR', 'YEARLY']:
                    revenue = (float(sub.stripe_price.unit_amount) / 12) * sub.quantity
                elif sub.stripe_price.interval in ['QUARTER', 'QUARTERLY']:
                    revenue = (float(sub.stripe_price.unit_amount) / 3) * sub.quantity
                else:
                    revenue = float(sub.stripe_price.unit_amount) * sub.quantity
                
                monthly_revenue += revenue
                
                plan_details.append({
                    'plan_name': plan_name,
                    'user_email': sub.user.email,
                    'revenue_contribution': revenue
                })
        
        recent_expiring = Subscription.objects.filter(
            Q(status__iexact='active') | Q(status__iexact='trialing'),
            current_period_end__lte=timezone.now() + timezone.timedelta(days=7)
        ).count()
        
        stats_data = {
            'total_subscriptions': total_subscriptions,
            'active_subscriptions': active_subscriptions,
            'monthly_revenue': round(monthly_revenue, 2),
            'by_plan': by_plan,
            'recent_expiring': recent_expiring
        }
        
        AuditLogService.log(
            user=request.user,
            action='ADMIN_VIEW_SUBSCRIPTION_STATS',
            category=AuditLogCategory.SUBSCRIPTION,
            description="Admin viewed subscription statistics",
            data={
                'total_subscriptions': total_subscriptions,
                'active_subscriptions': active_subscriptions,
                'monthly_revenue': round(monthly_revenue, 2),
                'recent_expiring': recent_expiring,
                'plan_details': plan_details[:10]
            },
            request=request
        )
        
        return Response(stats_data)


class PaymentViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PaymentSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Payment.objects.none()
        return Payment.objects.filter(user=self.request.user)
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_PAYMENTS_LIST',
            category=AuditLogCategory.PAYMENT,
            description="Payments history viewed",
            data={'count': len(response.data) if hasattr(response, 'data') else 0},
            request=request
        )
        
        return response
    
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        
        payment = self.get_object()
        AuditLogService.log(
            user=request.user,
            action='VIEW_PAYMENT_DETAILS',
            category=AuditLogCategory.PAYMENT,
            description=f"Payment details viewed: {payment.stripe_payment_intent_id}",
            resource=payment,
            data={'amount': float(payment.amount), 'status': payment.status},
            request=request
        )
        
        return response
    
    @action(detail=False, methods=['post'])
    def create_payment_intent(self, request):
        serializer = CreatePaymentIntentSerializer(data=request.data)
        if serializer.is_valid():
            service = StripeService()
            result = service.create_payment_intent(
                request.user,
                serializer.validated_data.get('amount'),
                serializer.validated_data.get('price_id'),
                serializer.validated_data.get('currency', 'eur')
            )
            
            if result:
                AuditLogService.log(
                    user=request.user,
                    action='CREATE_PAYMENT_INTENT',
                    category=AuditLogCategory.PAYMENT,
                    description=f"Payment intent created: {result['payment_intent_id']}",
                    data={
                        'amount': float(serializer.validated_data.get('amount', 0)),
                        'price_id': serializer.validated_data.get('price_id'),
                        'currency': serializer.validated_data.get('currency', 'eur')
                    },
                    request=request
                )
                
                return Response({
                    'client_secret': result['client_secret'],
                    'payment_intent_id': result['payment_intent_id'],
                    'payment': PaymentSerializer(result['payment']).data
                })
            
            return Response(
                {'error': 'Could not create payment intent'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class InvoiceViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = InvoiceSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Invoice.objects.none()

        user = self.request.user
        
        if user.role in [Roles.SUPERADMIN, Roles.ADMIN]:
            return Invoice.objects.all()
        
        if user.role == Roles.B2C:
            return Invoice.objects.filter(user=user)
        
        if user.role == Roles.B2B and hasattr(user, 'company_profile'):
            company = user.company_profile.company
            company_users = User.objects.filter(
                Q(company=company) |
                Q(managed_company=company)
            )
            return Invoice.objects.filter(user__in=company_users)
        
        if user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, 'team_member_profile'):
            company = user.team_member_profile.company
            company_users = User.objects.filter(
                Q(company=company) |
                Q(managed_company=company)
            )
            return Invoice.objects.filter(user__in=company_users)
        
        return Invoice.objects.none()
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_INVOICES_LIST',
            category=AuditLogCategory.BILLING,
            description="Invoices list viewed",
            data={'count': len(response.data) if hasattr(response, 'data') else 0},
            request=request
        )
        
        return response
    
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        
        invoice = self.get_object()
        AuditLogService.log(
            user=request.user,
            action='VIEW_INVOICE_DETAILS',
            category=AuditLogCategory.BILLING,
            description=f"Invoice details viewed: {invoice.number}",
            resource=invoice,
            data={'amount': float(invoice.amount_due), 'status': invoice.status},
            request=request
        )
        
        return response


import logging
logger = logging.getLogger(__name__)

@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(viewsets.GenericViewSet):
    permission_classes = [AllowAny]
    
    def post(self, request):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
        
        logger.info(f"Received webhook with signature: {sig_header}")
        
        if not sig_header:
            logger.error("No signature header found")
            return HttpResponse(status=400)
        
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )

            logger.info(f"Webhook verified: {event['type']}")

            # Stripe explicitly documents that a webhook may be delivered
            # more than once for the same event (retries, manual replays) -
            # handlers like EntitlementService.reset_b2b_balances() reset a
            # balance to the full grant amount unconditionally, so
            # reprocessing the same event would wipe out whatever was
            # already consumed since the first delivery. get_or_create is
            # the atomic check-and-insert; only a genuinely new event
            # proceeds to handle_webhook_event.
            processed_event, created = ProcessedStripeEvent.objects.get_or_create(
                stripe_event_id=event['id'],
                defaults={'event_type': event['type']},
            )
            if not created:
                logger.info(f"Skipping already-processed webhook event: {event['id']} ({event['type']})")
                return JsonResponse({'received': True, 'type': event['type'], 'duplicate': True})

            try:
                service = StripeService()
                result = service.handle_webhook_event(event)
            except Exception:
                # A genuine processing failure must not be mistaken for an
                # already-handled duplicate - remove the marker so Stripe's
                # automatic retry actually reprocesses this event, then let
                # the existing outer except Exception below log/500 it.
                processed_event.delete()
                raise

            AuditLogService.log_system(
                action='STRIPE_WEBHOOK_RECEIVED',
                category=AuditLogCategory.SYSTEM,
                description=f"Stripe webhook received: {event['type']}",
                data={
                    'event_type': event['type'],
                    'event_id': event['id'],
                    'result': 'handled' if result else 'unhandled'
                },
                severity=AuditLogSeverity.INFO
            )
            
            if result is not None:
                return JsonResponse({'received': True, 'type': event['type']})
            else:
                logger.warning(f"Unhandled event type: {event['type']}")
                return JsonResponse({'received': True, 'type': event['type']})
                
        except ValueError as e:
            logger.error(f"Invalid payload: {e}")
            
            AuditLogService.log_system(
                action='STRIPE_WEBHOOK_ERROR',
                category=AuditLogCategory.SYSTEM,
                description=f"Invalid webhook payload: {str(e)}",
                severity=AuditLogSeverity.ERROR
            )
            
            return HttpResponse(status=400)
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid signature: {e}")
            
            AuditLogService.log_system(
                action='STRIPE_WEBHOOK_ERROR',
                category=AuditLogCategory.SYSTEM,
                description=f"Invalid webhook signature: {str(e)}",
                severity=AuditLogSeverity.ERROR
            )
            
            return HttpResponse(status=400)
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            
            AuditLogService.log_system(
                action='STRIPE_WEBHOOK_ERROR',
                category=AuditLogCategory.SYSTEM,
                description=f"Unexpected webhook error: {str(e)}",
                severity=AuditLogSeverity.CRITICAL
            )
            
            return HttpResponse(status=500)



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def debug_subscription_data(request):
    """Debug endpoint to see raw subscription data"""
    user = request.user
    subscriptions = Subscription.objects.filter(user=user).select_related('stripe_price')
    
    data = []
    for sub in subscriptions:
        data.append({
            'id': str(sub.public_id),
            'stripe_price_id': sub.stripe_price_id,
            'stripe_price': {
                'id': str(sub.stripe_price.public_id) if sub.stripe_price else None,
                'name': sub.stripe_price.name if sub.stripe_price else None,
                'unit_amount': str(sub.stripe_price.unit_amount) if sub.stripe_price else None,
                'currency': sub.stripe_price.currency if sub.stripe_price else None,
                'interval': sub.stripe_price.interval if sub.stripe_price else None,
            } if sub.stripe_price else None,
            'status': sub.status,
            'current_period_end': sub.current_period_end,
            'serialized': SubscriptionSerializer(sub).data
        })
    
    AuditLogService.log(
        user=request.user,
        action='DEBUG_SUBSCRIPTION_DATA',
        category=AuditLogCategory.SUBSCRIPTION,
        description="Debug subscription data accessed",
        data={'count': len(data)},
        request=request
    )
    
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def retry_subscription_payment(request):
    subscription_id = request.data.get('subscription_id')
    
    try:
        subscription = get_by_identifier(
            Subscription.objects.filter(user=request.user),
            subscription_id,
        )
        
        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe_sub = stripe.Subscription.retrieve(
            subscription.stripe_subscription_id,
            expand=['latest_invoice']
        )
        
        result = {}
        if stripe_sub.latest_invoice and stripe_sub.latest_invoice.status == 'open':
            invoice = stripe_sub.latest_invoice
            result = {
                'requires_action': True,
                'hosted_invoice_url': invoice.hosted_invoice_url,
                'invoice_id': invoice.id
            }
            
            AuditLogService.log(
                user=request.user,
                action='RETRY_SUBSCRIPTION_PAYMENT',
                category=AuditLogCategory.PAYMENT,
                description=f"Payment retry initiated for subscription {subscription_id}",
                resource=subscription,
                data={'invoice_id': invoice.id},
                request=request
            )
        else:
            result = {
                'requires_action': False,
                'message': 'No action needed'
            }
        
        return Response(result)
            
    except Subscription.DoesNotExist:
        return Response({'error': 'Subscription not found'}, status=404)
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def debug_subscription_payment(request):
    user = request.user
    subscriptions = Subscription.objects.filter(user=user)
    
    results = []
    for sub in subscriptions:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe_sub = stripe.Subscription.retrieve(
            sub.stripe_subscription_id,
            expand=['latest_invoice.payment_intent', 'latest_invoice.payments.data.payment']
        )

        invoice = stripe_sub.latest_invoice if hasattr(stripe_sub, 'latest_invoice') else None
        payment_intent = None
        if invoice and hasattr(invoice, 'payment_intent') and invoice.payment_intent:
            payment_intent = invoice.payment_intent
        elif invoice and getattr(invoice, 'payments', None) and invoice.payments.data:
            pi_ref = getattr(invoice.payments.data[0].payment, 'payment_intent', None)
            if isinstance(pi_ref, str):
                payment_intent = stripe.PaymentIntent.retrieve(pi_ref)
            else:
                payment_intent = pi_ref
        
        results.append({
            'subscription_id': str(sub.public_id),
            'stripe_status': stripe_sub.status,
            'invoice_status': invoice.status if invoice else None,
            'payment_intent_status': payment_intent.status if payment_intent else None,
            'payment_intent_last_error': payment_intent.last_payment_error if payment_intent else None,
            'hosted_invoice_url': invoice.hosted_invoice_url if invoice else None,
        })
    
    AuditLogService.log(
        user=request.user,
        action='DEBUG_PAYMENT_DATA',
        category=AuditLogCategory.PAYMENT,
        description="Debug payment data accessed",
        data={'count': len(results)},
        request=request
    )
    
    return Response(results)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_subscription_status(request):
    subscription_id = request.data.get('subscription_id')
    
    if not subscription_id:
        return Response({'error': 'subscription_id required'}, status=400)
    
    try:
        subscription = get_by_identifier(
            Subscription.objects.filter(user=request.user),
            subscription_id,
        )
        
        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe_sub = stripe.Subscription.retrieve(
            subscription.stripe_subscription_id,
            expand=['latest_invoice.payment_intent']
        )
        
        old_status = subscription.status

        if subscription.status != stripe_sub.status.upper():
            subscription.status = stripe_sub.status.upper()
            subscription.save()
        
        if hasattr(stripe_sub, 'current_period_start') and stripe_sub.current_period_start:
            subscription.current_period_start = timezone.datetime.fromtimestamp(
                stripe_sub.current_period_start
            )
        if hasattr(stripe_sub, 'current_period_end') and stripe_sub.current_period_end:
            subscription.current_period_end = timezone.datetime.fromtimestamp(
                stripe_sub.current_period_end
            )
        subscription.save()
        
        AuditLogService.log(
            user=request.user,
            action='SYNC_SUBSCRIPTION_STATUS',
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Subscription status synced from Stripe",
            resource=subscription,
            data={
                'old_status': old_status,
                'new_status': stripe_sub.status,
                'stripe_status': stripe_sub.status
            },
            request=request
        )
        
        return Response({
            'message': 'Subscription synced successfully',
            'old_status': old_status,
            'new_status': stripe_sub.status,
            'stripe_status': stripe_sub.status
        })
        
    except Subscription.DoesNotExist:
        return Response({'error': 'Subscription not found'}, status=404)
    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_all_subscriptions(request):
    user = request.user
    subscriptions = Subscription.objects.filter(user=user)
    
    results = []
    for sub in subscriptions:
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
            
            results.append({
                'id': str(sub.public_id),
                'db_status': sub.status,
                'stripe_status': stripe_sub.status,
                'stripe_id': sub.stripe_subscription_id,
                'needs_sync': sub.status != stripe_sub.status
            })
        except Exception as e:
            results.append({
                'id': str(sub.public_id),
                'db_status': sub.status,
                'error': str(e)
            })
    
    AuditLogService.log(
        user=request.user,
        action='CHECK_ALL_SUBSCRIPTIONS',
        category=AuditLogCategory.SUBSCRIPTION,
        description="Checked all subscriptions status",
        data={'count': len(results)},
        request=request
    )
    
    return Response(results)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_all_subscriptions(request):
    """Sync all subscriptions for the current user"""
    user = request.user
    subscriptions = Subscription.objects.filter(user=user)
    
    synced_count = 0
    synced_details = []
    
    for sub in subscriptions:
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
            
            old_status = sub.status
            if sub.status != stripe_sub.status.upper():
                sub.status = stripe_sub.status.upper()
                sub.save()
                synced_count += 1
                synced_details.append({
                    'id': str(sub.public_id),
                    'old_status': old_status,
                    'new_status': stripe_sub.status.upper()
                })
        except Exception as e:
            print(f"Error syncing subscription {sub.public_id}: {e}")
    
    AuditLogService.log(
        user=request.user,
        action='SYNC_ALL_SUBSCRIPTIONS',
        category=AuditLogCategory.SUBSCRIPTION,
        description=f"Synced all subscriptions: {synced_count} updated",
        data={
            'synced_count': synced_count,
            'total': subscriptions.count(),
            'synced_details': synced_details
        },
        request=request
    )
    
    return Response({
        'message': f'Synced {synced_count} subscriptions',
        'total': subscriptions.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def spend_points(request):
    from .entitlement_serializers import AddonSpendSerializer
    from .entitlement_services import EntitlementService

    serializer = AddonSpendSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    addon_code = serializer.validated_data['addon_code']

    try:
        balance = EntitlementService.spend_points(user=request.user, addon_code=addon_code, actor=request.user)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'addon_code': addon_code,
        'remaining_points': balance.current_balance if balance else None,
    })
