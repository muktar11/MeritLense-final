# Payments & entitlements

This covers the `api/payments` app: Candidate Assessment Slots and Points
balances, B2B subscriptions, B2C one-time purchases, Custom Deal Records,
refunds, and the Stripe webhook integration that drives all of it. It does
not cover Stripe account setup itself beyond what's needed to keep this
subsystem working (see "Stripe webhook configuration" below).

## The two entitlement types

Slots and Points are two fully independent per-owner balances
(`PackageBalance`, `balance_type` = `SLOTS` or `POINTS`) — never converted
between each other. Slots are consumed once per assessment session actually
started (`EntitlementService.consume_slot`); Points are spent only on
optional add-ons, never on the core assessment.

**B2C** (`owner_user` set): one `PackageBalance` row per one-time purchase
(`source_payment` set), never expires. Multiple purchases accumulate as
separate rows, consumed oldest-first (`_consume_b2c`'s FIFO).

**B2B** (`owner_company` set): a single pooled row per `(company,
balance_type)`, reset in place every billing cycle — no rollover by default.
`source_subscription` tracks which subscription is currently responsible for
it.

## Where a B2B grant amount comes from

Every B2B grant lookup goes through one choke point,
`EntitlementService._resolve_grant(price, balance_type)`: if the `Price` has
a linked, active `DealRecord` (see below), its `slot_grant`/`points_grant`
wins; otherwise the `Price`'s own `slot_grant`/`points_grant` is used
(`None` means "no automated enforcement" — Enterprise/Starter default to
this until a Deal Record is linked). `_consume_b2b`, `reset_b2b_balances`,
`apply_upgrade_grant`, and `get_balance_summary` all call this rather than
reading `price.slot_grant` directly.

## B2B lifecycle

- **Renewal** (`reset_b2b_balances`, called from `handle_invoice_paid` on
  `invoice.payment_succeeded`): hard reset to the current grant — no
  rollover, unless the subscription's `Price` has a linked `DealRecord` with
  `rollover_allowed=True`, in which case the grant is added on top of
  whatever's unused instead of overwriting it.
- **Upgrade** (`apply_upgrade_grant`, called from `change_plan` after the
  proration invoice is paid): additive — the new plan's full grant is added
  to the existing balance, not an overwrite. Capped at one grant per billing
  cycle via the `BalanceTransaction` ledger itself (a reference scoped to
  `(subscription, current_period_start)` is naturally unique per cycle,
  since Stripe doesn't move the period on a mid-cycle plan swap).
  **Important interaction**: that proration invoice also fires a real
  `invoice.payment_succeeded` webhook. `handle_invoice_paid` skips calling
  `reset_b2b_balances` when the invoice's `billing_reason` is `"manual"` —
  that's the marker for exactly this kind of invoice — specifically so it
  doesn't undo the additive grant moments after applying it. This was a real
  bug found live (see "Lessons from live verification" below); don't remove
  that check without re-verifying against a real Stripe account.
- **Downgrade** (or an upgrade nobody actually paid extra for): the current
  balance is left untouched, rolling over to the new (lower) plan's amount
  at the next natural renewal.
- **Grace period / suspension**: `_active_recurring_subscription` treats
  `PAST_DUE` the same as `ACTIVE`/`TRIALING` (Stripe is still retrying
  payment — existing entitlements stay usable). Any other status (`UNPAID`,
  `CANCELED`, `INCOMPLETE_EXPIRED`, or no qualifying subscription at all)
  makes `_consume_b2b` raise instead of silently allowing unmetered usage —
  this blocks both Slots and Points consumption. Suspension never touches
  the balance itself, so reactivation restores access to exactly what was
  left.

## Points-based add-ons: Reserve → Consume → Release

`EntitlementService.reserve_points` deducts the balance immediately (via the
same `_consume_b2b`/`_consume_b2c` path as any other spend) and creates an
`AddonRequest` row in status `RESERVED`, tagging the ledger entry
`RESERVE` with `reference=f"addon-reservation:{addon_request.public_id}"`.
`confirm_addon` marks it `CONSUMED` (no further balance change — the
deduction already happened). `release_points` reverses it: it walks the
`RESERVE`-tagged `BalanceTransaction` rows for that reservation and credits
each one back — this is why release correctly un-splits a B2C reservation
that FIFO-spanned multiple purchase rows, without needing a join table.

`spend_points` (the only real caller today, `POST /points/spend`) reserves
then immediately confirms in the same call — there's no add-on fulfillment
pipeline in this codebase to defer confirmation to, so every spend still
gets the full audit trail even though nothing currently calls `release_points`
in production. Add real confirm/release endpoints only once something
async actually needs them — building them earlier is just unused surface.

## Refunds

`RefundEligibilityService.check(payment)` — checked **before** any Stripe
API call:

- Already refunded, or not `SUCCEEDED` → blocked, no override possible.
- **B2B**: blocked by default if the payment's linked subscription's `Price`
  has `billing_type == 'RECURRING'`. Pooled balances aren't attributable to
  one payment, so there's no automatic entitlement adjustment even under an
  override — only the Stripe refund itself happens.
- **B2C**: eligible only if every `PackageBalance` with `source_payment=payment`
  is fully unused (`current_balance == fixed_amount`). No partial refunds,
  no time window, each purchase judged independently.

  **Do not use `payment.subscription_id` alone to detect B2B.** A B2C
  one-time purchase also gets a bookkeeping `Subscription` row
  (`stripe_subscription_id = "one_time_<payment_intent_id>"`, see
  `_grant_one_time_package`) so the existing usage/limit machinery can be
  reused for one-time purchases too. The actual B2B/B2C signal is the
  linked subscription's `Price.billing_type` (`RECURRING` vs `ONE_TIME`).
  This was a real bug found live — every B2C refund was silently
  misclassified as non-refundable B2B until fixed.

`RefundService.refund_payment(payment, actor, override_reason_code=None)`
requires `override_reason_code` (`PLATFORM_ERROR` or
`CONFIRMED_BILLING_ERROR`) to proceed when ineligible. The override only
ever unblocks the Stripe refund call — it never changes how much
entitlement gets revoked. `revoke_unused_entitlement` only zeroes out what's
currently unused (floored at zero, never negative) and never restores
anything already consumed, override or not. The same revocation runs from
`handle_charge_refunded` (a refund issued directly from the Stripe
Dashboard), idempotent against the admin path already having handled it.

## Custom Deal Records (Enterprise / Starter / Trials)

`DealRecord` is one-to-one with `Price` (not linked from `Subscription`) —
Ops always mints a dedicated Product+Price per negotiated deal, so a
Subscription's terms are reached via `subscription.stripe_price.deal_record`.
Stripe Product/Price creation itself stays a manual, non-self-service Ops
action in Stripe Dashboard; the system never depends on Stripe metadata for
entitlement resolution, only this `Price ↔ DealRecord` link.

`deal_type` is one of `ENTERPRISE`, `STARTER`, `FREE_TRIAL`,
`CUSTOM_PAID_TRIAL` — mechanically Enterprise/Starter and the two trial
types behave the same way, the field is for record-keeping. `FREE_TRIAL` is
validated to exactly `slot_grant=2`, `unit_amount=0`, and rejected as a
duplicate if the same `company` (or another company sharing the same
`admin_user.email`) already has one. Managed via `AdminDealRecordViewSet`
(`/admin/deal-records`, SuperAdmin-only — same sensitivity tier as package
management).

## Admin tooling

All under `/api/v1/payments/admin/`, SuperAdmin-only unless noted:

- `admin/prices` — package CRUD (pre-existing).
- `admin/deal-records` — Custom Deal Record CRUD.
- `admin/payments` — read-only payment list + `POST {id}/refund`.
- `admin/package-balances` — read-only balance list + `POST {id}/adjust`
  (signed `delta` + required `reason`, ledgered as `ADMIN_ADJUST`) — the
  manual escape hatch for corrections a webhook/API call can't make
  automatically, e.g. a B2B pooled-balance correction after a
  `CONFIRMED_BILLING_ERROR` refund override.
- `admin/subscriptions` — read-only, `IsAdminOrSuperAdmin`.

Also registered in Django admin (`/admin/payments/`) for quick inspection:
`Subscription`, `Payment`, `PackageBalance`, `BalanceTransaction`,
`AddonRequest`, `DealRecord`.

## The `BalanceTransaction` ledger

Every balance change is recorded, `transaction_type` one of: `GRANT` (B2C
purchase, or an additive upgrade top-up), `RESET` (B2B renewal, overwrite),
`CONSUME` (a Slot used), `RESERVE`/`RELEASE` (an add-on reservation and its
reversal), `REFUND` (unused entitlement revoked), `ADMIN_ADJUST` (manual
correction). `reference` carries context (`session:<id>`,
`addon-reservation:<id>`, `upgrade:<sub>:<period>`, `refund:<intent>`,
`admin-adjust:<email>`) and is also used as an idempotency/reversal key in
several places — see `apply_upgrade_grant` and `release_points`.

## Stripe webhook configuration

`StripeWebhookView` (`POST /api/v1/payments/webhook`) verifies signatures
against `settings.STRIPE_WEBHOOK_SECRET` and is idempotent against
redelivery via `ProcessedStripeEvent` (keyed on Stripe's event ID). It
handles: `payment_intent.succeeded`, `payment_intent.payment_failed`,
`payment_method.attached`/`detached`, `customer.subscription.created`/
`updated`/`deleted`, `invoice.payment_succeeded`/`payment_failed`/`upcoming`,
`charge.refunded`.

**This only works if a webhook endpoint is actually registered in Stripe
for the mode (test/live) currently configured, subscribed to those event
types, with its signing secret in `.env`.** As of this writing the
production VM runs Stripe **test mode**; the endpoint was created via:

```
POST https://api.stripe.com/v1/webhook_endpoints
  url=https://api.meritlense.com/api/v1/payments/webhook
  enabled_events[]=<each event type above>
```

The returned `secret` goes into `STRIPE_WEBHOOK_SECRET` in the VM's `.env`,
then `sudo systemctl restart gunicorn`. **Switching to live mode needs a
separate live-mode webhook endpoint and secret** — Stripe keeps test and
live endpoints (and signing secrets) completely separate, same as it keeps
Prices/Products/Customers separate per mode. Recreating all `Price` rows in
live mode is also required before going live (see `docs/DEPLOYMENT.md` /
`AdminPriceViewSet`) — test-mode `stripe_price_id`s don't exist in live
mode.

### Lessons from live verification (2026-09-04)

Before the webhook endpoint existed, none of the webhook-driven paths ever
actually ran in production — they were only ever exercised by mocked unit
tests. Standing the endpoint up for the first time immediately surfaced two
real bugs that had been latent the whole time:

1. **Invoice → subscription lookup broke across a Stripe API version.**
   `handle_invoice_paid` originally read `invoice_data['subscription']` — a
   newer Stripe API version stopped sending that field entirely, nesting it
   under `invoice_data['parent']['subscription_details']['subscription']`
   instead. Every real B2B renewal was silently skipped as "no resolvable
   subscription." Fixed by `_extract_invoice_subscription_id`, which checks
   both shapes.
2. **The upgrade-grant race** described above (`billing_reason == 'manual'`).

If Stripe ever changes another payload shape this codebase reads directly
(grep for `invoice_data[`, `payment_intent[`, `subscription_data[` in
`services.py`), assume the same class of bug is possible and verify against
a real webhook delivery, not just mocks — mocks encode today's assumption
about the payload shape, not Stripe's actual current one.

## Known limitations (deliberately not built)

- No `SUSPENDED` status — Stripe's own `UNPAID`/`CANCELED`/
  `INCOMPLETE_EXPIRED` states are treated as "suspended" collectively.
- No automatic B2B pooled-balance adjustment on a refund override — use
  `admin/package-balances/{id}/adjust` manually if one is actually needed.
- No Stripe automation for Deal Records — creating the Product/Price is
  intentionally manual Ops work in Stripe Dashboard.
- Self-service refunds are out of scope — admin/superadmin-only by design.
