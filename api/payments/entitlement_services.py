from django.db import transaction
from django.utils import timezone

from .models import PackageBalance, BalanceTransaction, Subscription, AddonRequest

ADDON_POINTS_CATALOG = {
    "practical_simulation_test": 30,
    "video_introduction_recording": 30,
    "reference_verification": 30,
    "job_role_matching_recommendation": 20,
    "extended_assessment_insights": 25,
}


class EntitlementService:
    """
    Candidate Assessment Slots and Points Balance are two fully independent
    per-package entitlements (never converted between each other):
    - Slots are consumed once per assessment session actually started.
    - Points are spent only on optional add-ons, never on the core assessment.
    """

    @classmethod
    def resolve_owner(cls, session):
        """Returns ('COMPANY', company) for a B2B session, else ('USER', user)."""
        if session.organization_id:
            return "COMPANY", session.organization
        return "USER", session.candidate.created_by

    @classmethod
    def consume_slot(cls, session, actor=None):
        owner_type, owner = cls.resolve_owner(session)
        if owner_type == "COMPANY":
            cls._consume_b2b(owner, PackageBalance.SLOTS, reference=f"session:{session.public_id}", actor=actor)
        else:
            cls._consume_b2c(owner, PackageBalance.SLOTS, reference=f"session:{session.public_id}", actor=actor)

    @classmethod
    def reserve_points(cls, *, user, addon_code, actor=None):
        """Reserve Points for an add-on request (Sign-Off Section 5): deducts
        current_balance immediately, same as any spend, but only marks the
        request RESERVED - confirm_addon() or release_points() resolve it."""
        if addon_code not in ADDON_POINTS_CATALOG:
            raise ValueError("Unknown add-on code")
        points_cost = ADDON_POINTS_CATALOG[addon_code]

        owner_type = "COMPANY" if getattr(user, "role", None) in ("B2B", "B2B_TEAM") and hasattr(user, "company_profile") else "USER"
        with transaction.atomic():
            addon_request = AddonRequest.objects.create(
                owner_company=user.company_profile.company if owner_type == "COMPANY" else None,
                owner_user=None if owner_type == "COMPANY" else user,
                addon_code=addon_code,
                points_cost=points_cost,
                actor=actor,
            )
            reference = f"addon-reservation:{addon_request.public_id}"
            if owner_type == "COMPANY":
                cls._consume_b2b(
                    addon_request.owner_company, PackageBalance.POINTS, reference=reference,
                    actor=actor, amount=points_cost, transaction_type=BalanceTransaction.RESERVE,
                )
            else:
                cls._consume_b2c(
                    user, PackageBalance.POINTS, reference=reference,
                    actor=actor, amount=points_cost, transaction_type=BalanceTransaction.RESERVE,
                )
            return addon_request

    @classmethod
    def confirm_addon(cls, *, addon_request, actor=None):
        """Mark a reserved add-on request as successfully delivered. No
        balance change - the Points already left current_balance when the
        request was reserved; this just makes that deduction permanent."""
        if addon_request.status != AddonRequest.RESERVED:
            raise ValueError(f"Cannot confirm an add-on request in status {addon_request.status}")
        addon_request.status = AddonRequest.CONSUMED
        addon_request.resolved_at = timezone.now()
        addon_request.save(update_fields=["status", "resolved_at", "updated_at"])
        return addon_request

    @classmethod
    def release_points(cls, *, addon_request, actor=None):
        """Reverse a reservation on cancel/reject/fail/timeout, crediting
        back exactly what was reserved (per PackageBalance row touched, via
        the RESERVE ledger entries - correct even when a B2C reservation
        FIFO-split across multiple purchase rows). Idempotent if already
        released; refuses to release an already-confirmed (delivered)
        request, since consumed Points are never restored."""
        if addon_request.status == AddonRequest.RELEASED:
            return addon_request
        if addon_request.status != AddonRequest.RESERVED:
            raise ValueError(f"Cannot release an add-on request in status {addon_request.status}")

        reference = f"addon-reservation:{addon_request.public_id}"
        with transaction.atomic():
            reserved_txns = list(
                BalanceTransaction.objects.filter(reference=reference, transaction_type=BalanceTransaction.RESERVE)
            )
            for txn in reserved_txns:
                balance = PackageBalance.objects.select_for_update().get(pk=txn.balance_id)
                credit = -txn.amount
                balance.current_balance += credit
                balance.save(update_fields=["current_balance", "updated_at"])
                BalanceTransaction.objects.create(
                    balance=balance,
                    transaction_type=BalanceTransaction.RELEASE,
                    amount=credit,
                    balance_after=balance.current_balance,
                    reference=reference,
                    actor=actor,
                )
            addon_request.status = AddonRequest.RELEASED
            addon_request.resolved_at = timezone.now()
            addon_request.save(update_fields=["status", "resolved_at", "updated_at"])
        return addon_request

    @classmethod
    def spend_points(cls, *, user, addon_code, actor=None):
        """Immediate spend (today's only real caller: POST /points/spend).
        Reserves then immediately confirms - no fulfillment pipeline exists
        yet to defer confirmation to, but every spend still goes through the
        real Reserved -> Consumed lifecycle with a full audit trail."""
        addon_request = cls.reserve_points(user=user, addon_code=addon_code, actor=actor)
        cls.confirm_addon(addon_request=addon_request, actor=actor)
        if addon_request.owner_company_id:
            return PackageBalance.objects.filter(
                owner_company_id=addon_request.owner_company_id, balance_type=PackageBalance.POINTS
            ).first()
        return PackageBalance.objects.filter(
            owner_user_id=addon_request.owner_user_id, balance_type=PackageBalance.POINTS
        ).order_by('-updated_at').first()

    @classmethod
    def _active_recurring_subscription(cls, company):
        """PAST_DUE counts as the payment-failure grace period - Stripe is
        still retrying, and existing entitlements stay usable exactly like
        ACTIVE (Package Architecture Sign-Off). Anything else (UNPAID,
        CANCELED, INCOMPLETE_EXPIRED, or no subscription at all) is treated
        as suspended by _consume_b2b below."""
        return (
            Subscription.objects.filter(
                company=company,
                status__in=["ACTIVE", "TRIALING", "PAST_DUE"],
                stripe_price__billing_type="RECURRING",
            )
            .select_related("stripe_price")
            .order_by("-created_at")
            .first()
        )

    @classmethod
    def _resolve_grant(cls, price, balance_type):
        """A linked Deal Record's terms take priority over the Price's own
        grant when a custom deal exists - Stripe/the Price row is never the
        source of entitlement volume for a custom deal ("How Custom / Per
        Agreement Works" memo). Falls back to the Price's own grant when no
        deal is linked, so Growth/Business/self-serve plans are unaffected."""
        deal = getattr(price, 'deal_record', None)
        if deal is not None and deal.is_active:
            return deal.slot_grant if balance_type == PackageBalance.SLOTS else deal.points_grant
        return price.slot_grant if balance_type == PackageBalance.SLOTS else price.points_grant

    @classmethod
    def _consume_b2b(cls, company, balance_type, reference, actor=None, amount=1, transaction_type=BalanceTransaction.CONSUME):
        subscription = cls._active_recurring_subscription(company)
        if not subscription or not subscription.stripe_price:
            raise ValueError("No active subscription for this company - it may be suspended due to a failed payment")

        grant = cls._resolve_grant(subscription.stripe_price, balance_type)
        if grant is None:
            return None

        with transaction.atomic():
            balance, _ = PackageBalance.objects.select_for_update().get_or_create(
                owner_company=company,
                balance_type=balance_type,
                defaults={
                    "source_subscription": subscription,
                    "fixed_amount": grant,
                    "current_balance": grant,
                },
            )
            if balance.current_balance < amount:
                raise ValueError(f"No {balance_type.lower()} remaining on this plan for the current billing period")
            balance.current_balance -= amount
            balance.save(update_fields=["current_balance", "updated_at"])
            BalanceTransaction.objects.create(
                balance=balance,
                transaction_type=transaction_type,
                amount=-amount,
                balance_after=balance.current_balance,
                reference=reference,
                actor=actor,
            )
            return balance

    @classmethod
    def _consume_b2c(cls, user, balance_type, reference, actor=None, amount=1, transaction_type=BalanceTransaction.CONSUME):
        with transaction.atomic():
            rows = list(
                PackageBalance.objects.select_for_update()
                .filter(owner_user=user, balance_type=balance_type, current_balance__gt=0)
                .order_by("created_at")
            )
            remaining = amount
            touched = None
            for row in rows:
                if remaining <= 0:
                    break
                take = min(row.current_balance, remaining)
                row.current_balance -= take
                remaining -= take
                row.save(update_fields=["current_balance", "updated_at"])
                BalanceTransaction.objects.create(
                    balance=row,
                    transaction_type=transaction_type,
                    amount=-take,
                    balance_after=row.current_balance,
                    reference=reference,
                    actor=actor,
                )
                touched = row

            if remaining > 0:
                raise ValueError(f"No {balance_type.lower()} remaining - purchase a package to continue")
            return touched

    @classmethod
    def grant_b2c_balances(cls, payment, price):
        for balance_type, grant in (
            (PackageBalance.SLOTS, price.slot_grant),
            (PackageBalance.POINTS, price.points_grant),
        ):
            if grant is None:
                continue
            balance = PackageBalance.objects.create(
                owner_user=payment.user,
                balance_type=balance_type,
                source_payment=payment,
                fixed_amount=grant,
                current_balance=grant,
            )
            BalanceTransaction.objects.create(
                balance=balance,
                transaction_type=BalanceTransaction.GRANT,
                amount=grant,
                balance_after=grant,
                reference=f"payment:{payment.stripe_payment_intent_id}",
            )

    @classmethod
    def reset_b2b_balances(cls, subscription):
        if not subscription.stripe_price or not subscription.company:
            return
        deal = getattr(subscription.stripe_price, 'deal_record', None)
        rollover = deal is not None and deal.is_active and deal.rollover_allowed
        for balance_type in (PackageBalance.SLOTS, PackageBalance.POINTS):
            grant = cls._resolve_grant(subscription.stripe_price, balance_type)
            if grant is None:
                continue
            with transaction.atomic():
                balance, created = PackageBalance.objects.select_for_update().get_or_create(
                    owner_company=subscription.company,
                    balance_type=balance_type,
                    defaults={
                        "source_subscription": subscription,
                        "fixed_amount": grant,
                        "current_balance": grant,
                    },
                )
                balance.source_subscription = subscription
                if rollover and not created:
                    # Deal Record's Addendum allows rollover: unused balance
                    # carries forward, on top of this period's grant, rather
                    # than being overwritten (Sign-Off Section 9's default-no
                    # -rollover exception).
                    balance.fixed_amount += grant
                    balance.current_balance += grant
                else:
                    balance.fixed_amount = grant
                    balance.current_balance = grant
                balance.save(update_fields=["source_subscription", "fixed_amount", "current_balance", "updated_at"])
                BalanceTransaction.objects.create(
                    balance=balance,
                    transaction_type=BalanceTransaction.RESET,
                    amount=grant,
                    balance_after=balance.current_balance,
                    reference=f"subscription:{subscription.stripe_subscription_id}",
                )

    @classmethod
    def apply_upgrade_grant(cls, subscription):
        """Additive top-up for a mid-cycle B2B upgrade: the new plan's full
        entitlement is granted on top of any unused balance, capped at one
        full grant per billing cycle (Package Architecture memo, Section 3).

        Unlike reset_b2b_balances() (used on normal renewal, which resets to
        the plan amount with no rollover), this must not overwrite an unused
        balance - it only ever adds to it. The once-per-cycle cap is enforced
        via the BalanceTransaction ledger itself: a reference scoped to
        (subscription, current_period_start) is unique per cycle since
        Stripe does not move the period on a mid-cycle plan swap, only on an
        actual renewal.
        """
        if not subscription.stripe_price or not subscription.company:
            return
        period_key = subscription.current_period_start.isoformat()
        for balance_type in (PackageBalance.SLOTS, PackageBalance.POINTS):
            grant = cls._resolve_grant(subscription.stripe_price, balance_type)
            if grant is None:
                continue
            reference = f"upgrade:{subscription.stripe_subscription_id}:{period_key}"
            with transaction.atomic():
                balance, created = PackageBalance.objects.select_for_update().get_or_create(
                    owner_company=subscription.company,
                    balance_type=balance_type,
                    defaults={
                        "source_subscription": subscription,
                        "fixed_amount": grant,
                        "current_balance": grant,
                    },
                )
                if created:
                    BalanceTransaction.objects.create(
                        balance=balance,
                        transaction_type=BalanceTransaction.GRANT,
                        amount=grant,
                        balance_after=balance.current_balance,
                        reference=reference,
                    )
                    continue
                if BalanceTransaction.objects.filter(balance=balance, reference=reference).exists():
                    continue
                balance.source_subscription = subscription
                balance.fixed_amount += grant
                balance.current_balance += grant
                balance.save(update_fields=["source_subscription", "fixed_amount", "current_balance", "updated_at"])
                BalanceTransaction.objects.create(
                    balance=balance,
                    transaction_type=BalanceTransaction.GRANT,
                    amount=grant,
                    balance_after=balance.current_balance,
                    reference=reference,
                )

    @classmethod
    def get_balance_summary(cls, owner_type, owner):
        """Read-only snapshot for dashboard display. Returns a dict per balance_type."""
        summary = {}
        for balance_type in (PackageBalance.SLOTS, PackageBalance.POINTS):
            if owner_type == "COMPANY":
                balance = PackageBalance.objects.filter(owner_company=owner, balance_type=balance_type).first()
                if balance:
                    summary[balance_type] = {"remaining": balance.current_balance, "limit": balance.fixed_amount, "unlimited": False}
                    continue

                subscription = cls._active_recurring_subscription(owner)
                grant = None
                if subscription and subscription.stripe_price:
                    grant = cls._resolve_grant(subscription.stripe_price, balance_type)
                summary[balance_type] = {"remaining": None, "limit": None, "unlimited": subscription is not None and grant is None}
            else:
                rows = PackageBalance.objects.filter(owner_user=owner, balance_type=balance_type)
                if rows.exists():
                    remaining = sum(r.current_balance for r in rows)
                    total = sum(r.fixed_amount for r in rows)
                    summary[balance_type] = {"remaining": remaining, "limit": total, "unlimited": False}
                else:
                    summary[balance_type] = {"remaining": None, "limit": None, "unlimited": False}
        return summary
