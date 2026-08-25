from django.db import transaction

from .models import PackageBalance, BalanceTransaction, Subscription

ADDON_POINTS_CATALOG = {
    "practical_simulation_test": 30,
    "video_introduction_recording": 30,
    "reference_verification": 30,
    "job_role_matching_recommendation": 20,
    "behavioral_indicators_report": 25,
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
    def spend_points(cls, *, user, addon_code, actor=None):
        if addon_code not in ADDON_POINTS_CATALOG:
            raise ValueError("Unknown add-on code")
        points_cost = ADDON_POINTS_CATALOG[addon_code]

        owner_type = "COMPANY" if getattr(user, "role", None) in ("B2B", "B2B_TEAM") and hasattr(user, "company_profile") else "USER"
        if owner_type == "COMPANY":
            owner = user.company_profile.company
            return cls._consume_b2b(owner, PackageBalance.POINTS, reference=f"addon:{addon_code}", actor=actor, amount=points_cost)
        return cls._consume_b2c(user, PackageBalance.POINTS, reference=f"addon:{addon_code}", actor=actor, amount=points_cost)

    @classmethod
    def _active_recurring_subscription(cls, company):
        return (
            Subscription.objects.filter(
                company=company,
                status__in=["ACTIVE", "TRIALING"],
                stripe_price__billing_type="RECURRING",
            )
            .select_related("stripe_price")
            .order_by("-created_at")
            .first()
        )

    @classmethod
    def _consume_b2b(cls, company, balance_type, reference, actor=None, amount=1):
        subscription = cls._active_recurring_subscription(company)
        if not subscription or not subscription.stripe_price:
            return None

        grant = subscription.stripe_price.slot_grant if balance_type == PackageBalance.SLOTS else subscription.stripe_price.points_grant
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
                transaction_type=BalanceTransaction.CONSUME,
                amount=-amount,
                balance_after=balance.current_balance,
                reference=reference,
                actor=actor,
            )
            return balance

    @classmethod
    def _consume_b2c(cls, user, balance_type, reference, actor=None, amount=1):
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
                    transaction_type=BalanceTransaction.CONSUME,
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
        for balance_type, grant in (
            (PackageBalance.SLOTS, subscription.stripe_price.slot_grant),
            (PackageBalance.POINTS, subscription.stripe_price.points_grant),
        ):
            if grant is None:
                continue
            with transaction.atomic():
                balance, _ = PackageBalance.objects.select_for_update().get_or_create(
                    owner_company=subscription.company,
                    balance_type=balance_type,
                    defaults={
                        "source_subscription": subscription,
                        "fixed_amount": grant,
                        "current_balance": grant,
                    },
                )
                balance.source_subscription = subscription
                balance.fixed_amount = grant
                balance.current_balance = grant
                balance.save(update_fields=["source_subscription", "fixed_amount", "current_balance", "updated_at"])
                BalanceTransaction.objects.create(
                    balance=balance,
                    transaction_type=BalanceTransaction.RESET,
                    amount=grant,
                    balance_after=grant,
                    reference=f"subscription:{subscription.stripe_subscription_id}",
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
                    grant = subscription.stripe_price.slot_grant if balance_type == PackageBalance.SLOTS else subscription.stripe_price.points_grant
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
