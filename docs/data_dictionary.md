# Data Dictionary — YottaFlex Cart Recovery

> All data is fully synthetic. No real customer information is stored.
> Email domains use the `.test` TLD (RFC 2606). Phone numbers are fictitious.

---

## Table: `customers`

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `customer_id` | VARCHAR(32) | ✅ | `CUS-NNNNNN` (6-digit zero-padded) | ✅ PK | LIKE `CUS-%` |
| `full_name` | VARCHAR(100) | ✅ | Any non-empty string | ❌ | NOT NULL |
| `email` | VARCHAR(160) | ✅ | `customerNNNNN@example.test` | ✅ | NOT NULL; `.test` domain |
| `phone` | VARCHAR(20) | ✅ | `+1555NNNNNN` | ❌ | NOT NULL; fictitious |
| `created_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | ≤ NOW |

**Cardinality**: root entity; no FK dependencies.

---

## Table: `consents`

Materialised latest consent state per (customer, channel). Updated on each opt-in/opt-out event.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `consent_id` | VARCHAR(40) | ✅ | `CNS-NNNNNN-EMAIL` / `CNS-NNNNNN-SMS` | ✅ PK | — |
| `customer_id` | VARCHAR(32) | ✅ | FK → customers | ❌ | NOT NULL |
| `channel` | VARCHAR(10) | ✅ | `EMAIL`, `SMS` | ❌ | CHECK |
| `is_consented` | BOOLEAN | ✅ | `TRUE`, `FALSE` | ❌ | NOT NULL |
| `effective_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | NOT NULL |
| `source` | VARCHAR(30) | ✅ | `SIGNUP`, `WITHDRAWAL`, `REOPT_IN` | ❌ | NOT NULL |

**Cardinality**: 1:N (Customer → Consent), one row per channel per customer.
**Unique constraint**: `(customer_id, channel)`.

---

## Table: `consent_events`

Immutable consent history. Append-only; never update.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `event_id` | VARCHAR(40) | ✅ | `CEVT-NNNNNNN` | ✅ PK | — |
| `customer_id` | VARCHAR(32) | ✅ | FK → customers | ❌ | NOT NULL |
| `channel` | VARCHAR(10) | ✅ | `EMAIL`, `SMS` | ❌ | CHECK |
| `event_type` | VARCHAR(20) | ✅ | `OPT_IN`, `OPT_OUT` | ❌ | CHECK |
| `occurred_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | NOT NULL |
| `source` | VARCHAR(30) | ✅ | `SIGNUP`, `WITHDRAWAL`, `EC_WITHDRAWAL` | ❌ | NOT NULL |

**Cardinality**: 1:N (Customer → ConsentEvent). Multiple events per customer/channel.

---

## Table: `sessions`

A browser or app session for a customer.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `session_id` | VARCHAR(32) | ✅ | `SES-YYYY-NNNNNNN` | ✅ PK | — |
| `customer_id` | VARCHAR(32) | ✅ | FK → customers | ❌ | NOT NULL |
| `device_type` | VARCHAR(20) | ✅ | `DESKTOP`, `MOBILE`, `TABLET` | ❌ | CHECK |
| `started_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | ≤ last_activity_at |
| `last_activity_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | ≥ started_at |

**Cardinality**: 1:N (Customer → Session); 1:1 (Session → Cart).

---

## Table: `carts`

Cart lifecycle and stored totals. `total_amount` is derived from `cart_lines`.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `cart_id` | VARCHAR(40) | ✅ | `CART-YYYY-NNNNNNN` | ✅ PK | — |
| `customer_id` | VARCHAR(32) | ✅ | FK → customers | ❌ | NOT NULL |
| `session_id` | VARCHAR(32) | ✅ | FK → sessions | ❌ | NOT NULL |
| `status` | VARCHAR(20) | ✅ | `ACTIVE`, `ABANDONED`, `RECOVERED`, `PURCHASED` | ❌ | CHECK |
| `created_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | ≤ last_activity_at |
| `last_activity_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | ≥ created_at |
| `total_amount` | NUMERIC(12,2) | ✅ | ≥ 0.00 | ❌ | Derived from cart_lines |
| `abandonment_label` | BOOLEAN | ✅ | `TRUE`, `FALSE` | ❌ | Deterministic from idle time |
| `issue_type` | VARCHAR(60) | ❌ | `NULL` (clean), `STALE_CART_TOTAL`, `ABANDONED_AFTER_PURCHASE` | ❌ | Null for clean rows |

**Abandonment rule**: `abandonment_label = TRUE` iff `(last_activity_at - created_at) > 60 min` AND no completed order.

---

## Table: `cart_lines`

Line-item rows. Source of truth for cart arithmetic.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `cart_line_id` | VARCHAR(40) | ✅ | `CL-CART-YYYY-NNNNNNN-NN` | ✅ PK | — |
| `cart_id` | VARCHAR(40) | ✅ | FK → carts | ❌ | NOT NULL |
| `product_id` | VARCHAR(32) | ✅ | `PRD-NNNN` | ❌ | NOT NULL |
| `product_name` | VARCHAR(100) | ✅ | Non-empty string | ❌ | NOT NULL |
| `quantity` | INTEGER | ✅ | 1–5 | ❌ | > 0 |
| `unit_price` | NUMERIC(12,2) | ✅ | 5.00–500.00 | ❌ | > 0 |

**Derived constraint**: `SUM(quantity * unit_price)` per cart_id must equal `carts.total_amount` (±0.01) for clean rows.

---

## Table: `cart_status_events`

Immutable cart lifecycle transition log. Append-only.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `event_id` | VARCHAR(40) | ✅ | `CSE-NNNNNNN` | ✅ PK | — |
| `cart_id` | VARCHAR(40) | ✅ | FK → carts | ❌ | NOT NULL |
| `from_status` | VARCHAR(20) | ❌ | `NULL` (create), `ACTIVE`, `ABANDONED` | ❌ | — |
| `to_status` | VARCHAR(20) | ✅ | `ACTIVE`, `ABANDONED`, `RECOVERED`, `PURCHASED` | ❌ | NOT NULL |
| `occurred_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | NOT NULL |
| `reason` | VARCHAR(80) | ❌ | Free text | ❌ | — |

---

## Table: `campaigns`

Recovery campaign configuration.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `campaign_id` | VARCHAR(32) | ✅ | `CMP-YYYY-NNN` | ✅ PK | — |
| `name` | VARCHAR(100) | ✅ | Non-empty string | ❌ | NOT NULL |
| `channel` | VARCHAR(10) | ✅ | `EMAIL`, `SMS` | ❌ | CHECK |
| `delay_minutes` | INTEGER | ✅ | ≥ 0 (60/120/360/1440/2880) | ❌ | CHECK ≥ 0 |
| `active` | BOOLEAN | ✅ | `TRUE`, `FALSE` | ❌ | NOT NULL |

---

## Table: `message_templates`

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `template_id` | VARCHAR(50) | ✅ | `MSG-CMP-YYYY-NNN-01` | ✅ PK | — |
| `campaign_id` | VARCHAR(32) | ✅ | FK → campaigns | ❌ | NOT NULL |
| `subject` | VARCHAR(160) | ✅ | Non-empty string | ❌ | NOT NULL |
| `body` | TEXT | ✅ | Non-empty string with `{{customer_name}}`, `{{cart_total}}`, `{{cart_url}}` | ❌ | NOT NULL |

---

## Table: `frequency_cap_logs`

Per-customer daily send counts enforcing the max-3/channel/day cap.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `cap_log_id` | VARCHAR(40) | ✅ | `FCL-NNNNNNN` | ✅ PK | — |
| `customer_id` | VARCHAR(32) | ✅ | FK → customers | ❌ | NOT NULL |
| `channel` | VARCHAR(10) | ✅ | `EMAIL`, `SMS` | ❌ | NOT NULL |
| `send_date` | VARCHAR(10) | ✅ | `YYYY-MM-DD` | ❌ | NOT NULL |
| `send_count` | INTEGER | ✅ | 1–3 | ❌ | ≤ FREQ_CAP_DAILY |

**Unique constraint**: `(customer_id, channel, send_date)`.

---

## Table: `send_attempts`

Every scheduled, delivered, suppressed or frequency-capped message attempt.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `send_id` | VARCHAR(40) | ✅ | `SEND-YYYY-NNNNNNN` | ✅ PK | — |
| `campaign_id` | VARCHAR(32) | ✅ | FK → campaigns | ❌ | NOT NULL |
| `template_id` | VARCHAR(50) | ✅ | FK → message_templates | ❌ | NOT NULL |
| `customer_id` | VARCHAR(32) | ✅ | FK → customers | ❌ | NOT NULL |
| `cart_id` | VARCHAR(40) | ✅ | FK → carts | ❌ | NOT NULL |
| `channel` | VARCHAR(10) | ✅ | `EMAIL`, `SMS` | ❌ | CHECK |
| `status` | VARCHAR(20) | ✅ | `DELIVERED`, `SUPPRESSED`, `FREQ_CAP`, `BOUNCED` | ❌ | CHECK |
| `scheduled_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | NOT NULL |
| `sent_at` | TIMESTAMPTZ | ❌ | ISO 8601 UTC, NULL if not sent | ❌ | ≥ scheduled_at if present |
| `opened_at` | TIMESTAMPTZ | ❌ | ISO 8601 UTC, NULL | ❌ | ≥ sent_at if present |
| `clicked_at` | TIMESTAMPTZ | ❌ | ISO 8601 UTC, NULL | ❌ | ≥ opened_at if present |
| `issue_type` | VARCHAR(60) | ❌ | `NULL`, `MSG_AFTER_OPT_OUT` | ❌ | Null for clean rows |

**Suppression logic**:
- `SUPPRESSED` → `consents.is_consented = FALSE` at send time
- `FREQ_CAP` → ≥ 3 messages sent to same customer/channel on same UTC day

---

## Table: `orders`

Completed purchases.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `order_id` | VARCHAR(40) | ✅ | `ORD-YYYY-NNNNNNN` | ✅ PK | — |
| `customer_id` | VARCHAR(32) | ✅ | FK → customers | ❌ | NOT NULL |
| `cart_id` | VARCHAR(40) | ✅ | FK → carts | ❌ | NOT NULL |
| `total_amount` | NUMERIC(12,2) | ✅ | > 0 | ❌ | Copied from cart |
| `ordered_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | NOT NULL |
| `source` | VARCHAR(20) | ✅ | `DIRECT`, `RECOVERY`, `LATE_PURCHASE` | ❌ | CHECK |

**Attribution window**: recovery orders only attributed if `ordered_at` ≤ `clicked_at + 72 h`.

---

## Table: `attributions`

Campaign-to-order credit. One row per clean order (enforced by application logic; `ATTRIBUTION_DOUBLE_COUNT` edge cases are labelled).

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `attribution_id` | VARCHAR(40) | ✅ | `ATT-YYYY-NNNNNNN` | ✅ PK | — |
| `order_id` | VARCHAR(40) | ✅ | FK → orders | ❌ | NOT NULL |
| `campaign_id` | VARCHAR(32) | ✅ | FK → campaigns | ❌ | NOT NULL |
| `send_id` | VARCHAR(40) | ✅ | FK → send_attempts | ❌ | NOT NULL |
| `attribution_type` | VARCHAR(20) | ✅ | `LAST_CLICK`, `FIRST_CLICK`, `LINEAR` | ❌ | CHECK |
| `attributed_revenue` | NUMERIC(12,2) | ✅ | = order.total_amount (clean rows) | ❌ | Derived |
| `issue_type` | VARCHAR(60) | ❌ | `NULL`, `ATTRIBUTION_DOUBLE_COUNT` | ❌ | Null for clean rows |

---

## Table: `edge_cases`

Labelled anomaly records. **Never merged into input features.** Evaluation-only.

| Field | Type | Required | Allowed Values | Unique | Validation |
|---|---|---|---|---|---|
| `edge_case_id` | VARCHAR(40) | ✅ | `EC-NNNNNN` | ✅ PK | — |
| `issue_type` | VARCHAR(60) | ✅ | `MSG_AFTER_OPT_OUT`, `ABANDONED_AFTER_PURCHASE`, `ATTRIBUTION_DOUBLE_COUNT`, `STALE_CART_TOTAL` | ❌ | NOT NULL |
| `record_table` | VARCHAR(40) | ✅ | Table name containing the anomalous record | ❌ | NOT NULL |
| `record_id` | VARCHAR(40) | ✅ | PK of the anomalous record | ❌ | NOT NULL |
| `description` | TEXT | ✅ | Human-readable description | ❌ | NOT NULL |
| `expected_decision` | VARCHAR(80) | ✅ | Correct remediation action | ❌ | NOT NULL |
| `created_at` | TIMESTAMPTZ | ✅ | ISO 8601 UTC | ❌ | NOT NULL |

---

## Relationships Summary

```
Customer  1──N  Consent        (1:1 per channel)
Customer  1──N  ConsentEvent
Customer  1──N  Session
Customer  1──N  Cart
Customer  1──N  SendAttempt
Customer  1──N  Order
Session   1──1  Cart
Cart      1──N  CartLine       (source of truth for totals)
Cart      1──N  CartStatusEvent
Cart      1──N  SendAttempt
Cart      0──1  Order
Campaign  1──N  MessageTemplate
Campaign  1──N  SendAttempt
Campaign  1──N  Attribution
Order     1──1  Attribution    (clean rows; labelled double-count in edge_cases)
SendAttempt 1──1 Attribution   (via LAST_CLICK)
```
