import logging
import stripe
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from .models import (
    Price, Customer, PaymentMethod, 
    Subscription, Payment, Invoice
)

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
                price = Price.objects.get(id=price_id)
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
    



    def create_subscription(self, user, price_id, payment_method_id=None, 
                        trial_period_days=0, quantity=1):
        try:
            customer = self.get_or_create_customer(user)
            if not customer:
                logger.error(f"Failed to get/create customer for user {user.id}")
                return None
            
            try:
                price = Price.objects.get(id=price_id, is_active=True)
            except Price.DoesNotExist:
                logger.error(f"Price {price_id} not found or inactive")
                return None
            
            subscription_params = {
                'customer': customer.stripe_customer_id,
                'items': [{'price': price.stripe_price_id, 'quantity': quantity}],
                'expand': ['latest_invoice.payment_intent', 'latest_invoice'],
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
                status=stripe_subscription.status,
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
                if hasattr(invoice, 'payment_intent') and invoice.payment_intent:
                    payment_intent = invoice.payment_intent
                    payment = Payment.objects.create(
                        user=user,
                        customer=customer,
                        subscription=subscription,
                        stripe_payment_intent_id=payment_intent.id,
                        amount=Decimal(invoice.amount_due) / 100,
                        currency=invoice.currency,
                        status='SUCCEEDED' if invoice.paid else 'PENDING',
                        paid_at=timezone.now() if invoice.paid else None,
                        metadata=payment_intent.metadata
                    )
                    
                    subscription.latest_invoice = payment
                    subscription.save()
            
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
            subscription = Subscription.objects.get(id=subscription_id)
            stripe_subscription_id = subscription.stripe_subscription_id
            
            update_params = {}
            
            if price_id or quantity:
                items = []
                if price_id:
                    new_price = Price.objects.get(id=price_id)
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
                subscription.status = stripe_subscription.status
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
            subscription = Subscription.objects.get(id=subscription_id)
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
            return payment
        except Exception as e:
            logger.error(f"Error handling payment succeeded: {e}")
            return None
    
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
                subscription.status = subscription_data['status']
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
            subscription.status = subscription_data['status']
            
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
    
    def handle_invoice_paid(self, invoice_data):
        """Handle invoice paid - this should trigger subscription status update"""
        try:
            invoice, created = Invoice.objects.update_or_create(
                stripe_invoice_id=invoice_data['id'],
                defaults={
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
            
            if invoice_data.get('subscription'):
                subscription = Subscription.objects.filter(
                    stripe_subscription_id=invoice_data['subscription']
                ).first()
                if subscription:
                    invoice.subscription = subscription
                    invoice.save()
                    
                    if subscription.status == 'incomplete':
                        subscription.status = 'active'
                        subscription.save()
                        logger.info(f"Subscription {subscription.id} status updated to active after payment")
            
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