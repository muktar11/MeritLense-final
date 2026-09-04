import logging
import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from api.core.public_ids import get_by_identifier
from api.core.constants import SubscriptionStatus
from .models import (
    Price, Customer, PaymentMethod,
    Subscription, Payment, Invoice
)
from .refund_services import RefundService

import logging
logger = logging.getLogger(__name__)

class StripeService:
    
    def __init__(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY
    
    def create_customer(self, user):
        try:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.get_full_name(),
                metadata={
                    'user_id': user.id,
                    'user_email': user.email
                }
            )
            
            db_customer, created = Customer.objects.update_or_create(
                user=user,
                defaults={
                    'stripe_customer_id': customer.id,
                    'email': user.email,
                    'name': user.get_full_name()
                }
            )
            
            return db_customer
        except stripe.error.StripeError as e:
            print(f"Stripe error creating customer: {e}")
            return None
    
    def get_or_create_customer(self, user):
        try:
            customer = Customer.objects.get(user=user)
            return customer
        except Customer.DoesNotExist:
            return self.create_customer(user)
    
    def create_setup_intent(self, user):
        customer = self.get_or_create_customer(user)
        
        try:
            setup_intent = stripe.SetupIntent.create(
                customer=customer.stripe_customer_id,
                metadata={
                    'user_id': user.id,
                    'customer_id': customer.id
                }
            )
            return {
                'client_secret': setup_intent.client_secret,
                'setup_intent_id': setup_intent.id
            }
        except stripe.error.StripeError as e:
            print(f"Stripe error creating setup intent: {e}")
            return None
    
    def attach_payment_method(self, user, payment_method_id, set_default=False):
        customer = self.get_or_create_customer(user)
        
        try:
            payment_method = stripe.PaymentMethod.attach(
                payment_method_id,
                customer=customer.stripe_customer_id
            )
            
            if set_default:
                stripe.Customer.modify(
                    customer.stripe_customer_id,
                    invoice_settings={
                        'default_payment_method': payment_method_id
                    }
                )
                customer.default_payment_method_id = payment_method_id
                customer.save()
            
            db_payment_method, created = PaymentMethod.objects.update_or_create(
                stripe_payment_method_id=payment_method_id,
                defaults={
                    'customer': customer,
                    'method_type': 'CARD',
                    'card_brand': payment_method.card.brand,
                    'card_last4': payment_method.card.last4,
                    'card_exp_month': payment_method.card.exp_month,
                    'card_exp_year': payment_method.card.exp_year,
                    'is_default': set_default,
                    'is_active': True,
                    'billing_details': payment_method.billing_details
                }
            )
            
            return db_payment_method
        except stripe.error.StripeError as e:
            print(f"Stripe error attaching payment method: {e}")
            return None
    
    def list_payment_methods(self, user):
        customer = self.get_or_create_customer(user)
        
        try:
            payment_methods = stripe.PaymentMethod.list(
                customer=customer.stripe_customer_id,
                type='card'
            )
            
            for pm in payment_methods.data:
                PaymentMethod.objects.update_or_create(
                    stripe_payment_method_id=pm.id,
                    defaults={
                        'customer': customer,
                        'method_type': 'CARD',
                        'card_brand': pm.card.brand,
                        'card_last4': pm.card.last4,
                        'card_exp_month': pm.card.exp_month,
                        'card_exp_year': pm.card.exp_year,
                        'is_active': True,
                        'billing_details': pm.billing_details
                    }
                )
            
            return PaymentMethod.objects.filter(
                customer=customer,
                is_active=True
            ).order_by('-is_default')
        except stripe.error.StripeError as e:
            print(f"Stripe error listing payment methods: {e}")
            return []
    
    def detach_payment_method(self, payment_method_id):
        try:
            stripe.PaymentMethod.detach(payment_method_id)
            
            PaymentMethod.objects.filter(
                stripe_payment_method_id=payment_method_id
            ).update(is_active=False)
            
            return True
        except stripe.error.StripeError as e:
            print(f"Stripe error detaching payment method: {e}")
            return False
    
    def create_payment_intent(self, user, amount=None, price_id=None, currency='eur'):
        customer = self.get_or_create_customer(user)
        
        if price_id:
            try:
                price = get_by_identifier(Price.objects.all(), price_id)
                amount = price.unit_amount
            except Price.DoesNotExist:
                return None
        
        if not amount:
            return None
        
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),
                currency=currency,
                customer=customer.stripe_customer_id,
                setup_future_usage='off_session',
                metadata={
                    'user_id': user.id,
                    'customer_id': customer.id,
                    'price_id': price_id
                }
            )
            
            payment = Payment.objects.create(
                user=user,
                customer=customer,
                stripe_payment_intent_id=intent.id,
                amount=amount,
                currency=currency,
                status='PENDING'
            )
            
            return {
                'client_secret': intent.client_secret,
                'payment_intent_id': intent.id,
                'payment': payment
            }
        except stripe.error.StripeError as e:
            print(f"Stripe error creating payment intent: {e}")
            return None
    
    def confirm_payment_intent(self, payment_intent_id):
        try:
            intent = stripe.PaymentIntent.confirm(payment_intent_id)
            return intent
        except stripe.error.StripeError as e:
            print(f"Stripe error confirming payment intent: {e}")
            return None

    def _build_stripe_product_metadata(self, data):
        metadata = {
            'target_user_type': data.get('target_user_type', 'BOTH'),
        }
        if data.get('min_company_size'):
            metadata['min_company_size'] = data['min_company_size']
        if data.get('max_company_size'):
            metadata['max_company_size'] = data['max_company_size']
        feature_limits = data.get('feature_limits') or {}
        for key in ('candidate_limit', 'evaluation_limit', 'team_member_limit'):
            if feature_limits.get(key) is not None:
                metadata[key] = str(feature_limits[key])
        if data.get('slot_grant') is not None:
            metadata['slot_grant'] = str(data['slot_grant'])
        if data.get('points_grant') is not None:
            metadata['points_grant'] = str(data['points_grant'])
        return metadata

    def create_price_and_product(self, data):
        """Create a brand-new Stripe Product + Price for an admin-authored package.

        Package fields that don't exist on Stripe's Price object (feature_limits,
        evaluation_tier, task_observation_enabled, etc.) are mirrored into the
        Stripe Product's metadata for visibility in the Stripe dashboard, but the
        local `Price` row remains the single source of truth the app reads from.
        """
        product = stripe.Product.create(
            name=data['name'],
            metadata=self._build_stripe_product_metadata(data),
        )

        price_params = {
            'product': product.id,
            'unit_amount': int(Decimal(str(data['unit_amount'])) * 100),
            'currency': data.get('currency', 'eur'),
            'metadata': data.get('metadata', {}) or {},
        }

        if data.get('billing_type', 'RECURRING') != 'ONE_TIME':
            # Our BillingInterval choices (MONTHLY/QUARTERLY/YEARLY) don't map
            # 1:1 onto Stripe's accepted recurring[interval] values (day/week/
            # month/year) — QUARTERLY in particular has no Stripe equivalent
            # and must be expressed as interval=month, interval_count=3.
            stripe_interval, base_count = {
                'MONTHLY': ('month', 1),
                'QUARTERLY': ('month', 3),
                'YEARLY': ('year', 1),
            }.get(data.get('interval', 'MONTHLY'), ('month', 1))
            price_params['recurring'] = {
                'interval': stripe_interval,
                'interval_count': base_count * data.get('interval_count', 1),
            }

        stripe_price = stripe.Price.create(**price_params)

        return stripe_price, product

    def update_price_metadata(self, price, data):
        """Update a package's non-pricing fields in place (name, limits, tier, flags, is_active).

        Does not touch amount/currency/interval/billing_type — those require
        retire_and_replace_price because Stripe Price objects are immutable.
        """
        product_updates = {}
        if 'name' in data:
            product_updates['name'] = data['name']
        product_updates['metadata'] = self._build_stripe_product_metadata({**price.__dict__, **data})

        stripe.Product.modify(price.stripe_product_id, **product_updates)

        if 'is_active' in data and data['is_active'] != price.is_active:
            stripe.Price.modify(price.stripe_price_id, active=data['is_active'])

        return price

    def retire_and_replace_price(self, old_price, data):
        """Archive the old Stripe Price and local row, create a new one with the new terms.

        Stripe Prices are immutable once created, so any amount/currency/interval/
        billing_type change must create a brand-new Stripe Price + local Price row.
        The old row is kept (is_active=False), not deleted, so existing
        Subscription.stripe_price FKs keep resolving to the terms they actually bought.
        """
        try:
            stripe.Price.modify(old_price.stripe_price_id, active=False)
        except stripe.error.StripeError as e:
            logger.warning(f"Could not archive old Stripe price {old_price.stripe_price_id}: {e}")

        old_price.is_active = False
        old_price.save(update_fields=['is_active'])

        merged = {
            'name': data.get('name', old_price.name),
            'target_user_type': data.get('target_user_type', old_price.target_user_type),
            'min_company_size': data.get('min_company_size', old_price.min_company_size),
            'max_company_size': data.get('max_company_size', old_price.max_company_size),
            'unit_amount': data.get('unit_amount', old_price.unit_amount),
            'currency': data.get('currency', old_price.currency),
            'billing_type': data.get('billing_type', old_price.billing_type),
            'interval': data.get('interval', old_price.interval),
            'interval_count': data.get('interval_count', old_price.interval_count),
            'feature_limits': data.get('feature_limits', old_price.feature_limits),
            'slot_grant': data.get('slot_grant', old_price.slot_grant),
            'points_grant': data.get('points_grant', old_price.points_grant),
            'metadata': data.get('metadata', old_price.metadata),
        }

        stripe_price, product = self.create_price_and_product(merged)

        new_price = Price.objects.create(
            name=merged['name'],
            stripe_price_id=stripe_price.id,
            stripe_product_id=product.id,
            target_user_type=merged['target_user_type'],
            min_company_size=merged['min_company_size'],
            max_company_size=merged['max_company_size'],
            unit_amount=merged['unit_amount'],
            currency=merged['currency'],
            billing_type=merged['billing_type'],
            interval=merged['interval'],
            interval_count=merged['interval_count'],
            evaluation_tier=data.get('evaluation_tier', old_price.evaluation_tier),
            task_observation_enabled=data.get('task_observation_enabled', old_price.task_observation_enabled),
            features=data.get('features', old_price.features),
            feature_limits=merged['feature_limits'],
            slot_grant=merged['slot_grant'],
            points_grant=merged['points_grant'],
            is_active=True,
            metadata=merged['metadata'],
        )

        return new_price



    def create_subscription(self, user, price_id, payment_method_id=None, 
                        trial_period_days=0, quantity=1):
        try:
            customer = self.get_or_create_customer(user)
            if not customer:
                logger.error(f"Failed to get/create customer for user {user.id}")
                return None
            
            try:
                price = get_by_identifier(Price.objects.filter(is_active=True), price_id)
            except Price.DoesNotExist:
                logger.error(f"Price {price_id} not found or inactive")
                return None
            
            subscription_params = {
                'customer': customer.stripe_customer_id,
                'items': [{'price': price.stripe_price_id, 'quantity': quantity}],
                # Stripe's newer API versions ("clover") dropped Invoice.payment_intent
                # in favor of Invoice.payments[].payment.payment_intent — expand both
                # paths so this works regardless of which API version the account is on.
                # Stripe caps expand depth at 4 levels, so `.payment_intent` on the
                # nested payment is fetched as a plain ID rather than expanded further.
                'expand': ['latest_invoice.payment_intent', 'latest_invoice.payments.data.payment'],
                'metadata': {
                    'user_id': str(user.id),
                    'price_id': str(price_id),
                    'user_email': user.email
                }
            }
            
            if trial_period_days > 0:
                subscription_params['trial_period_days'] = trial_period_days
            
            if payment_method_id:
                subscription_params['default_payment_method'] = payment_method_id
                subscription_params['off_session'] = True
            
            logger.info(f"Creating Stripe subscription with params: {subscription_params}")
            
            stripe_subscription = stripe.Subscription.create(**subscription_params)
            logger.info(f"Stripe subscription created: {stripe_subscription.id}")
            
            interval = price.interval.lower() if price.interval else 'month'
            interval_count = price.interval_count or 1
            
            current_period_start = timezone.now()
            
            if interval == 'month':
                current_period_end = current_period_start + timezone.timedelta(days=30 * interval_count)
            elif interval == 'year':
                current_period_end = current_period_start + timezone.timedelta(days=365 * interval_count)
            elif interval == 'week':
                current_period_end = current_period_start + timezone.timedelta(days=7 * interval_count)
            else:
                current_period_end = current_period_start + timezone.timedelta(days=30)
            
            logger.info(f"Using calculated periods: start={current_period_start}, end={current_period_end}")
            
            subscription = Subscription.objects.create(
                user=user,
                customer=customer,
                stripe_subscription_id=stripe_subscription.id,
                stripe_price=price,
                status=stripe_subscription.status.upper(),
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                quantity=quantity,
                metadata=stripe_subscription.metadata
            )
            
            if hasattr(stripe_subscription, 'trial_start') and stripe_subscription.trial_start:
                subscription.trial_start = timezone.datetime.fromtimestamp(
                    stripe_subscription.trial_start
                )
            if hasattr(stripe_subscription, 'trial_end') and stripe_subscription.trial_end:
                subscription.trial_end = timezone.datetime.fromtimestamp(
                    stripe_subscription.trial_end
                )
            
            if hasattr(user, 'company_profile') and user.company_profile:
                subscription.company = user.company_profile.company
            
            subscription.save()
            
            if hasattr(stripe_subscription, 'latest_invoice') and stripe_subscription.latest_invoice:
                invoice = stripe_subscription.latest_invoice
                payment_intent_id = None
                payment_intent_metadata = {}
                if hasattr(invoice, 'payment_intent') and invoice.payment_intent:
                    payment_intent_id = invoice.payment_intent.id
                    payment_intent_metadata = invoice.payment_intent.metadata
                elif getattr(invoice, 'payments', None) and invoice.payments.data:
                    pi_ref = getattr(invoice.payments.data[0].payment, 'payment_intent', None)
                    if pi_ref:
                        payment_intent_id = pi_ref if isinstance(pi_ref, str) else pi_ref.id

                if payment_intent_id:
                    is_paid = invoice.status == 'paid' if hasattr(invoice, 'status') else bool(getattr(invoice, 'paid', False))
                    Payment.objects.create(
                        user=user,
                        customer=customer,
                        subscription=subscription,
                        stripe_payment_intent_id=payment_intent_id,
                        amount=Decimal(invoice.amount_due) / 100,
                        currency=invoice.currency,
                        status='SUCCEEDED' if is_paid else 'PENDING',
                        paid_at=timezone.now() if is_paid else None,
                        metadata=payment_intent_metadata
                    )
            
            logger.info(f"Subscription created successfully in database: {subscription.id}")
            return subscription
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating subscription: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error creating subscription: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def update_subscription(self, subscription_id, price_id=None, quantity=None,
                           cancel_at_period_end=None):
        try:
            subscription = get_by_identifier(Subscription.objects.all(), subscription_id)
            stripe_subscription_id = subscription.stripe_subscription_id
            
            update_params = {}
            
            if price_id or quantity:
                items = []
                if price_id:
                    new_price = get_by_identifier(Price.objects.all(), price_id)
                    items.append({
                        'id': subscription.stripe_price.stripe_price_id,
                        'price': new_price.stripe_price_id
                    })
                if quantity:
                    if not items:
                        items.append({
                            'id': subscription.stripe_price.stripe_price_id,
                            'quantity': quantity
                        })
                    else:
                        items[0]['quantity'] = quantity
                
                update_params['items'] = items
            
            if cancel_at_period_end is not None:
                update_params['cancel_at_period_end'] = cancel_at_period_end
            
            if update_params:
                stripe_subscription = stripe.Subscription.modify(
                    stripe_subscription_id,
                    **update_params
                )
                
                if price_id:
                    subscription.stripe_price = new_price
                subscription.status = stripe_subscription.status.upper()
                subscription.current_period_end = timezone.datetime.fromtimestamp(
                    stripe_subscription.current_period_end
                )
                subscription.save()
            
            return subscription
        except (Subscription.DoesNotExist, Price.DoesNotExist, stripe.error.StripeError) as e:
            print(f"Error updating subscription: {e}")
            return None
    
    def cancel_subscription(self, subscription_id, at_period_end=True):
        try:
            subscription = get_by_identifier(Subscription.objects.all(), subscription_id)
            result = subscription.cancel(at_period_end)
            return result
        except Subscription.DoesNotExist:
            return False
    
    def handle_webhook_event(self, event):
        event_type = event['type']
        data = event['data']['object']
        
        logger.info(f"Processing webhook event: {event_type}")
        
        event_handlers = {
            'payment_intent.succeeded': self.handle_payment_succeeded,
            'payment_intent.payment_failed': self.handle_payment_failed,
            'payment_method.attached': self.handle_payment_method_attached,
            'payment_method.detached': self.handle_payment_method_detached,
            'customer.subscription.created': self.handle_subscription_created,
            'customer.subscription.updated': self.handle_subscription_updated,
            'customer.subscription.deleted': self.handle_subscription_deleted,
            'invoice.payment_succeeded': self.handle_invoice_paid,
            'invoice.payment_failed': self.handle_invoice_failed,
            'invoice.upcoming': self.handle_invoice_upcoming,
            'charge.refunded': self.handle_charge_refunded,
        }
        
        handler = event_handlers.get(event_type)
        if handler:
            try:
                return handler(data)
            except Exception as e:
                logger.error(f"Error handling {event_type}: {e}")
                return None
        else:
            logger.info(f"No handler for event type: {event_type}")
            return None
    
    def handle_payment_succeeded(self, payment_intent):
        try:
            payment, created = Payment.objects.get_or_create(
                stripe_payment_intent_id=payment_intent['id'],
                defaults={
                    'amount': Decimal(payment_intent['amount']) / 100,
                    'currency': payment_intent['currency'],
                    'status': 'SUCCEEDED',
                    'paid_at': timezone.now(),
                    'receipt_url': payment_intent.get('receipt_url', ''),
                }
            )

            if not created:
                payment.status = 'SUCCEEDED'
                payment.paid_at = timezone.now()
                payment.receipt_url = payment_intent.get('receipt_url', '')
                payment.save()

            logger.info(f"Payment succeeded: {payment_intent['id']}")

            self._grant_one_time_package(payment, payment_intent)

            return payment
        except Exception as e:
            logger.error(f"Error handling payment succeeded: {e}")
            return None

    def _grant_one_time_package(self, payment, payment_intent):
        """For a one-time package purchase, create a local Subscription bookkeeping
        row on payment success so the existing usage/limit machinery (built for
        Stripe Subscriptions) can be reused unchanged for one-time purchases.

        Idempotent: skips if this payment has already granted a subscription
        (webhooks can be delivered more than once for the same event).
        """
        if payment.subscription_id:
            return

        price_id = (payment_intent.get('metadata') or {}).get('price_id')
        if not price_id:
            return

        try:
            price = get_by_identifier(Price.objects.all(), price_id)
        except Price.DoesNotExist:
            logger.warning(f"One-time package grant skipped: price {price_id} not found")
            return

        if price.billing_type != 'ONE_TIME':
            return

        validity_days = int((price.metadata or {}).get('validity_days', 365))
        now = timezone.now()

        subscription = Subscription.objects.create(
            user=payment.user,
            customer=payment.customer,
            stripe_subscription_id=f"one_time_{payment.stripe_payment_intent_id}",
            stripe_price=price,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=now + timezone.timedelta(days=validity_days),
            current_usage={},
            metadata={'purchase_type': 'one_time', 'payment_id': payment.id},
        )

        if hasattr(payment.user, 'company_profile') and payment.user.company_profile:
            subscription.company = payment.user.company_profile.company
            subscription.save(update_fields=['company'])

        payment.subscription = subscription
        payment.save(update_fields=['subscription'])

        from .entitlement_services import EntitlementService
        EntitlementService.grant_b2c_balances(payment, price)

        logger.info(f"Granted one-time package '{price.name}' to user {payment.user.id} via payment {payment.id}")
    
    def handle_payment_failed(self, payment_intent):
        try:
            payment = Payment.objects.filter(
                stripe_payment_intent_id=payment_intent['id']
            ).first()
            
            if payment:
                payment.status = 'FAILED'
                payment.save()
                logger.info(f"Payment failed: {payment_intent['id']}")
            
            return payment
        except Exception as e:
            logger.error(f"Error handling payment failed: {e}")
            return None
    
    def handle_payment_method_attached(self, payment_method_data):
        try:
            customer_id = payment_method_data.get('customer')
            if not customer_id:
                return None
            
            customer = Customer.objects.filter(
                stripe_customer_id=customer_id
            ).first()
            
            if customer:
                pm, created = PaymentMethod.objects.update_or_create(
                    stripe_payment_method_id=payment_method_data['id'],
                    defaults={
                        'customer': customer,
                        'method_type': 'CARD',
                        'card_brand': payment_method_data['card']['brand'],
                        'card_last4': payment_method_data['card']['last4'],
                        'card_exp_month': payment_method_data['card']['exp_month'],
                        'card_exp_year': payment_method_data['card']['exp_year'],
                        'is_active': True,
                        'billing_details': payment_method_data.get('billing_details', {})
                    }
                )
                logger.info(f"Payment method attached: {payment_method_data['id']}")
            
            return pm
        except Exception as e:
            logger.error(f"Error handling payment method attached: {e}")
            return None
    
    def handle_payment_method_detached(self, payment_method_data):
        try:
            PaymentMethod.objects.filter(
                stripe_payment_method_id=payment_method_data['id']
            ).update(is_active=False)
            logger.info(f"Payment method detached: {payment_method_data['id']}")
            return True
        except Exception as e:
            logger.error(f"Error handling payment method detached: {e}")
            return None
    
    def handle_subscription_created(self, subscription_data):
        try:
            subscription = Subscription.objects.filter(
                stripe_subscription_id=subscription_data['id']
            ).first()
            
            if subscription:
                subscription.status = subscription_data['status'].upper()
                subscription.current_period_end = timezone.datetime.fromtimestamp(
                    subscription_data['current_period_end']
                )
                subscription.save()
                logger.info(f"Subscription created: {subscription_data['id']}")
            
            return subscription
        except Exception as e:
            logger.error(f"Error handling subscription created: {e}")
            return None
    

    def handle_subscription_updated(self, subscription_data):
        """Handle subscription updated"""
        try:
            subscription = Subscription.objects.get(
                stripe_subscription_id=subscription_data['id']
            )
            old_status = subscription.status
            subscription.status = subscription_data['status'].upper()

            if 'current_period_start' in subscription_data:
                subscription.current_period_start = timezone.datetime.fromtimestamp(
                    subscription_data['current_period_start']
                )
            if 'current_period_end' in subscription_data:
                subscription.current_period_end = timezone.datetime.fromtimestamp(
                    subscription_data['current_period_end']
                )
            
            subscription.cancel_at_period_end = subscription_data.get('cancel_at_period_end', False)
            subscription.save()
            
            logger.info(f"Subscription updated: {subscription_data['id']} - {old_status} -> {subscription_data['status']}")
            return subscription
        except Subscription.DoesNotExist:
            logger.warning(f"Subscription not found: {subscription_data['id']}")
            return None
        except Exception as e:
            logger.error(f"Error handling subscription updated: {e}")
            return None
    
    def handle_subscription_deleted(self, subscription_data):
        try:
            subscription = Subscription.objects.get(
                stripe_subscription_id=subscription_data['id']
            )
            subscription.status = 'CANCELED'
            subscription.canceled_at = timezone.now()
            subscription.save()
            
            logger.info(f"Subscription deleted: {subscription_data['id']}")
            return subscription
        except Subscription.DoesNotExist:
            logger.warning(f"Subscription not found: {subscription_data['id']}")
            return None
        except Exception as e:
            logger.error(f"Error handling subscription deleted: {e}")
            return None

    def handle_charge_refunded(self, charge_data):
        """A refund issued directly from the Stripe Dashboard (not through
        our admin refund action) - syncs local state only, no Stripe call.
        Idempotent against RefundService.refund_payment() already having
        processed the same payment (checked via Payment.refunded_at)."""
        payment_intent_id = charge_data.get('payment_intent')
        if not payment_intent_id:
            return None
        payment = Payment.objects.filter(stripe_payment_intent_id=payment_intent_id).first()
        if not payment or payment.refunded_at:
            return payment
        with transaction.atomic():
            payment.status = 'REFUNDED'
            payment.refunded_at = timezone.now()
            payment.save(update_fields=["status", "refunded_at", "updated_at"])
            RefundService.revoke_unused_entitlement(payment)
        logger.info(f"Payment refunded via Stripe: {payment.stripe_payment_intent_id}")
        return payment

    def _extract_invoice_subscription_id(self, invoice_data):
        """Stripe moved the invoice-to-subscription reference across API
        versions: older payloads carry a top-level 'subscription' field,
        newer ones nest it under parent.subscription_details.subscription
        instead (confirmed live - a real invoice.payment_succeeded webhook
        had no top-level 'subscription' at all). Check both so a future
        API version bump can't silently break this again the same way -
        matches the same defensive multi-shape handling already used for
        invoice.payment_intent in create_subscription() above."""
        subscription_id = invoice_data.get('subscription')
        if subscription_id:
            return subscription_id
        parent = invoice_data.get('parent') or {}
        subscription_details = parent.get('subscription_details') or {}
        return subscription_details.get('subscription')

    def handle_invoice_paid(self, invoice_data):
        """Handle invoice paid - records the invoice (the source of truth for
        subscription billing history, including renewals - see
        AdminRevenueTrendView) and updates the subscription status.

        Invoice.user/customer are required (not nullable), so they must come
        from the subscription this invoice belongs to - an invoice with no
        resolvable subscription is skipped rather than raising an
        IntegrityError, since there's no other way to know who it's for.
        """
        try:
            subscription = None
            subscription_id = self._extract_invoice_subscription_id(invoice_data)
            if subscription_id:
                subscription = Subscription.objects.filter(
                    stripe_subscription_id=subscription_id
                ).first()

            if not subscription:
                logger.warning(
                    f"Invoice {invoice_data.get('id')} has no resolvable subscription - skipping "
                    f"(Invoice.user/customer can't be set without one)"
                )
                return None

            invoice, created = Invoice.objects.update_or_create(
                stripe_invoice_id=invoice_data['id'],
                defaults={
                    'user': subscription.user,
                    'customer': subscription.customer,
                    'subscription': subscription,
                    'number': invoice_data['number'],
                    'status': 'PAID',
                    'amount_due': Decimal(invoice_data['amount_due']) / 100,
                    'amount_paid': Decimal(invoice_data['amount_paid']) / 100,
                    'amount_remaining': Decimal(invoice_data['amount_remaining']) / 100,
                    'currency': invoice_data['currency'],
                    'paid_at': timezone.now(),
                    'invoice_pdf': invoice_data.get('invoice_pdf', ''),
                    'hosted_invoice_url': invoice_data.get('hosted_invoice_url', ''),
                }
            )

            if subscription.status == 'INCOMPLETE':
                subscription.status = 'ACTIVE'
                subscription.save()
                logger.info(f"Subscription {subscription.id} status updated to active after payment")

            # A 'manual' billing_reason is the mid-cycle proration invoice
            # change_plan() creates and pays directly for an upgrade - its
            # entitlement was already granted additively via
            # apply_upgrade_grant() at that time. Confirmed live: Stripe
            # still sends a real invoice.payment_succeeded webhook for that
            # same invoice moments later, and resetting again here would
            # silently overwrite the additive top-up with a flat reset to
            # just the new plan's grant, discarding whatever was unused
            # before the upgrade. Genuine renewals (billing_reason
            # 'subscription_cycle', 'subscription_create', etc.) are
            # unaffected and still reset as before.
            if invoice_data.get('billing_reason') != 'manual':
                from .entitlement_services import EntitlementService
                EntitlementService.reset_b2b_balances(subscription)

            logger.info(f"Invoice paid: {invoice_data['id']}")
            return invoice
        except Exception as e:
            logger.error(f"Error handling invoice paid: {e}")
            return None
    
    def handle_invoice_failed(self, invoice_data):
        try:
            invoice = Invoice.objects.get(stripe_invoice_id=invoice_data['id'])
            invoice.status = 'UNCOLLECTIBLE'
            invoice.save()
            
            logger.info(f"Invoice payment failed: {invoice_data['id']}")
            return invoice
        except Invoice.DoesNotExist:
            logger.warning(f"Invoice not found: {invoice_data['id']}")
            return None
        except Exception as e:
            logger.error(f"Error handling invoice failed: {e}")
            return None
    
    def handle_invoice_upcoming(self, invoice_data):
        logger.info(f"Upcoming invoice: {invoice_data['id']}")
        return None
