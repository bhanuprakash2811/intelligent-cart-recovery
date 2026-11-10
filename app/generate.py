"""
YottaFlex Cart Recovery — Synthetic Data Generator
===================================================
Implements all 10 steps from the data-generation spec.

Fixed seed : 20261022  (override with RANDOM_SEED env var)
Scale      : 20,000 customers / 50,000 carts / 20,000 abandoned / 10,000 sends
             Override with TOTAL_CUSTOMERS, TOTAL_CARTS, TARGET_ABANDONED_CARTS,
             TOTAL_SEND_ATTEMPTS env vars.

ID format
---------
  Customer       CUS-000001
  Session        SES-2026-000001
  Cart           CART-2026-000001
  CartLine       CL-CART-2026-000001-01
  ConsentEvent   CEVT-000001
  CartStatusEvt  CSE-000001
  Campaign       CMP-2026-001
  Template       MSG-CART-2026-000001-01
  FreqCapLog     FCL-000001
  SendAttempt    SEND-2026-000001
  Order          ORD-2026-000001
  Attribution    ATT-2026-000001
  EdgeCase       EC-000001

Usage
-----
  python -m app.generate            # full dataset
  python -m app.generate --small    # 100-cart smoke test
  python -m app.generate --seed 42  # custom seed
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import insert, text

from .db import engine, Base
from . import models as m

# ---------------------------------------------------------------------------
# Configuration (can be overridden by env vars or --small flag)
# ---------------------------------------------------------------------------
SEED         = int(os.getenv("RANDOM_SEED",            "20261022"))
N_CUSTOMERS  = int(os.getenv("TOTAL_CUSTOMERS",         "20000"))
N_CARTS      = int(os.getenv("TOTAL_CARTS",             "50000"))
N_ABANDONED  = int(os.getenv("TARGET_ABANDONED_CARTS",  "20000"))
N_SENDS      = int(os.getenv("TOTAL_SEND_ATTEMPTS",     "10000"))

NOW   = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
FIRST = NOW - timedelta(days=60)

ABANDONMENT_MINUTES = 60          # cart idle > 60 min → abandoned
ATTRIBUTION_WINDOW  = 72 * 60     # 72-hour attribution window (minutes)
FREQ_CAP_DAILY      = 3           # max messages per customer per channel per day

YEAR = str(NOW.year)              # "2026" used in IDs

OUTPUT_DIR = Path("output")

# ---------------------------------------------------------------------------
# Product catalogue (500 products)
# ---------------------------------------------------------------------------
_PRODUCT_NAMES = [
    "Wireless Headphones", "Smart Watch", "Running Shoes", "Coffee Maker",
    "Yoga Mat", "Bluetooth Speaker", "Laptop Stand", "Ergonomic Chair",
    "Water Bottle", "Resistance Bands", "Protein Powder", "Backpack",
    "Sunglasses", "Portable Charger", "Air Purifier", "Standing Desk",
    "Noise-Cancelling Earbuds", "Mechanical Keyboard", "Gaming Mouse",
    "LED Desk Lamp",
]


def _product(pid: int) -> tuple[str, str]:
    base = _PRODUCT_NAMES[(pid - 1) % len(_PRODUCT_NAMES)]
    return f"PRD-{pid:04d}", f"{base} {pid:04d}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _money(cents: int) -> Decimal:
    return Decimal(str(cents / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _bulk_insert(conn, table, rows: list[dict], chunk: int = 2000) -> None:
    for i in range(0, len(rows), chunk):
        conn.execute(insert(table), rows[i : i + chunk])


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _export_csv(rows: list[dict], filename: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    if not rows:
        path.write_text("")
        return path
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
def run(
    seed:        int  = SEED,
    n_customers: int  = N_CUSTOMERS,
    n_carts:     int  = N_CARTS,
    n_abandoned: int  = N_ABANDONED,
    n_sends:     int  = N_SENDS,
    reset:       bool = True,
    export_csv:  bool = True,
) -> dict[str, Any]:
    """
    Generate and persist the full synthetic dataset.
    Returns a summary dict of row counts.
    """
    if not (0 <= n_abandoned <= n_carts <= 500_000):
        raise ValueError("n_abandoned must be <= n_carts <= 500,000")

    rng = random.Random(seed)

    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    # -----------------------------------------------------------------------
    # STEP 1-3  Master / parent tables
    # -----------------------------------------------------------------------
    customers:       list[dict] = []
    consents:        list[dict] = []
    consent_events:  list[dict] = []
    cevt_counter = 0

    for i in range(1, n_customers + 1):
        cid     = f"CUS-{i:06d}"
        created = FIRST + timedelta(minutes=rng.randint(0, 55 * 24 * 60))
        customers.append(dict(
            customer_id = cid,
            full_name   = f"Customer {i:05d}",
            email       = f"customer{i:05d}@example.test",
            phone       = f"+155500{i:06d}",
            created_at  = created,
        ))

        for channel in ("EMAIL", "SMS"):
            # Initial opt-in probability
            is_in = rng.random() < (0.78 if channel == "EMAIL" else 0.55)
            event_type = "OPT_IN" if is_in else "OPT_OUT"
            cevt_counter += 1
            evt_id = f"CEVT-{cevt_counter:07d}"
            consent_events.append(dict(
                event_id    = evt_id,
                customer_id = cid,
                channel     = channel,
                event_type  = event_type,
                occurred_at = created,
                source      = "SIGNUP",
            ))
            # Materialised latest state
            consent_id = f"CNS-{i:06d}-{channel}"
            consents.append(dict(
                consent_id   = consent_id,
                customer_id  = cid,
                channel      = channel,
                is_consented = is_in,
                effective_at = created,
                source       = "SIGNUP",
            ))

    # -----------------------------------------------------------------------
    # STEP 2  Sessions, Carts, CartLines, CartStatusEvents
    # -----------------------------------------------------------------------
    sessions:          list[dict] = []
    carts:             list[dict] = []
    cart_lines:        list[dict] = []
    cart_status_events: list[dict] = []
    cse_counter = 0

    # Build a consent lookup for send-time checks
    # consent_lookup[(customer_id, channel)] = is_consented (bool)
    consent_lookup: dict[tuple[str, str], bool] = {}
    for c in consents:
        consent_lookup[(c["customer_id"], c["channel"])] = c["is_consented"]

    DEVICES = ["DESKTOP", "MOBILE", "TABLET"]
    DEVICE_W = [0.45, 0.42, 0.13]

    for i in range(1, n_carts + 1):
        customer_num = ((i - 1) % n_customers) + 1
        cust_id  = f"CUS-{customer_num:06d}"
        sess_id  = f"SES-{YEAR}-{i:07d}"
        cart_id  = f"CART-{YEAR}-{i:07d}"

        created  = FIRST + timedelta(minutes=rng.randint(0, 55 * 24 * 60))
        device   = rng.choices(DEVICES, DEVICE_W)[0]

        is_abandoned = i <= n_abandoned
        if is_abandoned:
            idle_min = rng.randint(ABANDONMENT_MINUTES + 5, 240)
            last     = created + timedelta(minutes=idle_min)
            status   = "ABANDONED"
        else:
            last   = created + timedelta(minutes=rng.randint(1, 45))
            status = "ACTIVE"

        # Clamp to NOW
        if last > NOW:
            last = NOW - timedelta(minutes=ABANDONMENT_MINUTES + 5 if is_abandoned else 5)

        sessions.append(dict(
            session_id       = sess_id,
            customer_id      = cust_id,
            device_type      = device,
            started_at       = created,
            last_activity_at = last,
        ))

        # Cart lines (1–4 items)
        item_count  = rng.randint(1, 4)
        cart_total  = Decimal("0.00")
        for j in range(1, item_count + 1):
            pid, pname = _product(rng.randint(1, 500))
            price = _money(rng.randint(500, 50_000))
            qty   = rng.randint(1, 5)
            cart_total += price * qty
            cart_lines.append(dict(
                cart_line_id = f"CL-{cart_id}-{j:02d}",
                cart_id      = cart_id,
                product_id   = pid,
                product_name = pname,
                quantity     = qty,
                unit_price   = price,
            ))

        carts.append(dict(
            cart_id          = cart_id,
            customer_id      = cust_id,
            session_id       = sess_id,
            status           = status,
            created_at       = created,
            last_activity_at = last,
            total_amount     = cart_total,
            abandonment_label = is_abandoned,
            issue_type       = None,
        ))

        # Cart status event: CREATED → current status
        cse_counter += 1
        cart_status_events.append(dict(
            event_id    = f"CSE-{cse_counter:07d}",
            cart_id     = cart_id,
            from_status = None,
            to_status   = "ACTIVE",
            occurred_at = created,
            reason      = "CART_CREATED",
        ))
        if is_abandoned:
            cse_counter += 1
            cart_status_events.append(dict(
                event_id    = f"CSE-{cse_counter:07d}",
                cart_id     = cart_id,
                from_status = "ACTIVE",
                to_status   = "ABANDONED",
                occurred_at = last + timedelta(minutes=ABANDONMENT_MINUTES),
                reason      = f"NO_ACTIVITY_{ABANDONMENT_MINUTES}MIN",
            ))

    # -----------------------------------------------------------------------
    # STEP 2  Campaigns and Templates
    # -----------------------------------------------------------------------
    campaigns:  list[dict] = []
    templates:  list[dict] = []

    campaign_defs = [
        (f"CMP-{YEAR}-001", "Initial Cart Reminder",        "EMAIL", 60),
        (f"CMP-{YEAR}-002", "High-Value Cart Reminder",     "EMAIL", 120),
        (f"CMP-{YEAR}-003", "SMS Recovery Nudge",           "SMS",   360),
        (f"CMP-{YEAR}-004", "Final Email Reminder",         "EMAIL", 1440),
        (f"CMP-{YEAR}-005", "SMS Last-Chance",              "SMS",   2880),
    ]
    template_subjects = {
        "EMAIL": "You left something in your cart!",
        "SMS":   "Your cart is waiting — tap to complete your order",
    }
    template_bodies = {
        "EMAIL": (
            "Hi {{customer_name}},\n\n"
            "You left {{item_count}} item(s) worth {{cart_total}} in your cart.\n"
            "Complete your purchase before they sell out.\n\n"
            "Shop now: {{cart_url}}\n\n"
            "To unsubscribe reply STOP."
        ),
        "SMS": (
            "Hi {{customer_name}}! Your cart ({{cart_total}}) is waiting. "
            "Complete now: {{cart_url}} Reply STOP to opt out."
        ),
    }

    for cmp_id, name, channel, delay in campaign_defs:
        campaigns.append(dict(
            campaign_id   = cmp_id,
            name          = name,
            channel       = channel,
            delay_minutes = delay,
            active        = True,
        ))
        tpl_id = f"MSG-{cmp_id}-01"
        templates.append(dict(
            template_id = tpl_id,
            campaign_id = cmp_id,
            subject     = template_subjects[channel],
            body        = template_bodies[channel],
        ))

    # Build campaign lookup by channel
    email_campaigns = [c for c in campaign_defs if c[2] == "EMAIL"]
    sms_campaigns   = [c for c in campaign_defs if c[2] == "SMS"]

    # -----------------------------------------------------------------------
    # STEP 2  Orders for non-abandoned carts (direct purchases)
    # -----------------------------------------------------------------------
    orders:       list[dict] = []
    attributions: list[dict] = []
    order_counter = 0
    attr_counter  = 0

    # Cart lookup by cart_id
    cart_lookup = {c["cart_id"]: c for c in carts}

    for i in range(n_abandoned + 1, n_carts + 1):
        cart = carts[i - 1]
        if rng.random() < 0.30:
            order_counter += 1
            oid      = f"ORD-{YEAR}-{order_counter:07d}"
            ordered  = cart["last_activity_at"] + timedelta(minutes=rng.randint(1, 30))
            if ordered > NOW:
                ordered = NOW - timedelta(minutes=1)
            orders.append(dict(
                order_id     = oid,
                customer_id  = cart["customer_id"],
                cart_id      = cart["cart_id"],
                total_amount = cart["total_amount"],
                ordered_at   = ordered,
                source       = "DIRECT",
            ))

    # -----------------------------------------------------------------------
    # STEP 2  Send attempts for abandoned carts (with consent + freq-cap checks)
    # -----------------------------------------------------------------------
    sends:       list[dict] = []
    freq_cap_logs: list[dict] = []
    fcl_counter  = 0

    # Frequency-cap tracker: (customer_id, channel, date_str) → count
    freq_tracker: dict[tuple[str, str, str], int] = {}

    abandoned_indices = list(range(n_abandoned))
    rng.shuffle(abandoned_indices)
    send_indices = abandoned_indices[: min(n_sends, n_abandoned)]

    send_counter = 0
    for cart_idx in send_indices:
        cart    = carts[cart_idx]
        cust_id = cart["customer_id"]
        cart_id = cart["cart_id"]

        # Choose channel (75% email, 25% SMS)
        channel = "EMAIL" if rng.random() < 0.75 else "SMS"
        consented = consent_lookup.get((cust_id, channel), False)

        # Pick campaign/template for channel
        camp_pool = email_campaigns if channel == "EMAIL" else sms_campaigns
        cmp_id, _, _, delay = rng.choice(camp_pool)
        tpl_id = f"MSG-{cmp_id}-01"

        scheduled = cart["last_activity_at"] + timedelta(minutes=delay)
        if scheduled > NOW:
            scheduled = NOW - timedelta(minutes=1)

        send_date = scheduled.strftime("%Y-%m-%d")
        freq_key  = (cust_id, channel, send_date)
        daily_cnt = freq_tracker.get(freq_key, 0)

        # Determine send status
        if not consented:
            status  = "SUPPRESSED"
            sent_at = None
        elif daily_cnt >= FREQ_CAP_DAILY:
            status  = "FREQ_CAP"
            sent_at = None
        else:
            status  = "DELIVERED"
            sent_at = scheduled + timedelta(minutes=1)
            freq_tracker[freq_key] = daily_cnt + 1

        # Open / click events (only for DELIVERED)
        opened_at  = None
        clicked_at = None
        if status == "DELIVERED" and sent_at:
            max_lag = max(2, int((NOW - sent_at).total_seconds() // 60))
            if rng.random() < 0.45 and max_lag >= 2:
                open_lag   = rng.randint(2, min(60, max_lag))
                opened_at  = sent_at + timedelta(minutes=open_lag)
                click_lag  = rng.randint(1, min(30, max(1, max_lag - open_lag)))
                if rng.random() < 0.35 and (max_lag - open_lag) >= 1:
                    clicked_at = opened_at + timedelta(minutes=click_lag)

        send_counter += 1
        send_id = f"SEND-{YEAR}-{send_counter:07d}"
        sends.append(dict(
            send_id      = send_id,
            campaign_id  = cmp_id,
            template_id  = tpl_id,
            customer_id  = cust_id,
            cart_id      = cart_id,
            channel      = channel,
            status       = status,
            scheduled_at = scheduled,
            sent_at      = sent_at,
            opened_at    = opened_at,
            clicked_at   = clicked_at,
            issue_type   = None,
        ))

        # Attribution: clicked → purchase within 72 h
        if clicked_at and rng.random() < 0.35:
            purchase_lag = rng.randint(5, min(ATTRIBUTION_WINDOW, max(5, int((NOW - clicked_at).total_seconds() // 60))))
            ordered_at   = clicked_at + timedelta(minutes=purchase_lag)
            if ordered_at > NOW:
                ordered_at = NOW

            order_counter += 1
            attr_counter  += 1
            oid  = f"ORD-{YEAR}-{order_counter:07d}"
            orders.append(dict(
                order_id     = oid,
                customer_id  = cust_id,
                cart_id      = cart_id,
                total_amount = cart["total_amount"],
                ordered_at   = ordered_at,
                source       = "RECOVERY",
            ))
            attributions.append(dict(
                attribution_id    = f"ATT-{YEAR}-{attr_counter:07d}",
                order_id          = oid,
                campaign_id       = cmp_id,
                send_id           = send_id,
                attribution_type  = "LAST_CLICK",
                attributed_revenue = cart["total_amount"],
                issue_type        = None,
            ))
            # Update cart status → RECOVERED
            cse_counter += 1
            cart_status_events.append(dict(
                event_id    = f"CSE-{cse_counter:07d}",
                cart_id     = cart_id,
                from_status = "ABANDONED",
                to_status   = "RECOVERED",
                occurred_at = ordered_at,
                reason      = "CAMPAIGN_CONVERSION",
            ))

    # -----------------------------------------------------------------------
    # Build frequency cap log rows from tracker
    # -----------------------------------------------------------------------
    for (cust_id, channel, send_date), cnt in freq_tracker.items():
        fcl_counter += 1
        freq_cap_logs.append(dict(
            cap_log_id  = f"FCL-{fcl_counter:07d}",
            customer_id = cust_id,
            channel     = channel,
            send_date   = send_date,
            send_count  = cnt,
        ))

    # -----------------------------------------------------------------------
    # STEP 5  Inject labelled edge cases
    # -----------------------------------------------------------------------
    edge_cases: list[dict] = []
    ec_counter = 0

    # --- EC1: Message sent after opt-out (50 cases) ---
    # Pick 50 delivered sends, inject a consent withdrawal before scheduled_at
    # Mark the send with issue_type=MSG_AFTER_OPT_OUT
    delivered_sends = [s for s in sends if s["status"] == "DELIVERED"]
    ec1_targets = rng.sample(delivered_sends, min(50, len(delivered_sends)))
    for s in ec1_targets:
        cust_id = s["customer_id"]
        channel = s["channel"]
        # Insert a withdrawal event before scheduled_at
        withdrawal_at = s["scheduled_at"] - timedelta(minutes=rng.randint(5, 60))
        cevt_counter += 1
        consent_events.append(dict(
            event_id    = f"CEVT-{cevt_counter:07d}",
            customer_id = cust_id,
            channel     = channel,
            event_type  = "OPT_OUT",
            occurred_at = withdrawal_at,
            source      = "EC_WITHDRAWAL",
        ))
        s["issue_type"] = "MSG_AFTER_OPT_OUT"
        ec_counter += 1
        edge_cases.append(dict(
            edge_case_id      = f"EC-{ec_counter:06d}",
            issue_type        = "MSG_AFTER_OPT_OUT",
            record_table      = "send_attempts",
            record_id         = s["send_id"],
            description       = "Message delivered after customer opted out of this channel",
            expected_decision = "SUPPRESS_RETROACTIVELY",
            created_at        = NOW,
        ))

    # --- EC2: Cart abandoned after purchase (25 cases) ---
    # Pick 25 carts with RECOVERY orders, mark them also ABANDONED
    recovery_cart_ids = {o["cart_id"] for o in orders if o["source"] == "RECOVERY"}
    ec2_pool = [c for c in carts if c["cart_id"] in recovery_cart_ids
                and c["issue_type"] is None]
    ec2_targets = rng.sample(ec2_pool, min(25, len(ec2_pool)))
    for c in ec2_targets:
        c["issue_type"] = "ABANDONED_AFTER_PURCHASE"
        c["abandonment_label"] = True
        c["status"] = "ABANDONED"
        ec_counter += 1
        edge_cases.append(dict(
            edge_case_id      = f"EC-{ec_counter:06d}",
            issue_type        = "ABANDONED_AFTER_PURCHASE",
            record_table      = "carts",
            record_id         = c["cart_id"],
            description       = "Cart labelled abandoned even though a recovery order exists",
            expected_decision = "RECLASSIFY_AS_RECOVERED",
            created_at        = NOW,
        ))

    # --- EC3: Attribution double-counted (30 cases) ---
    # For 30 recovery orders, add a second attribution to a different campaign
    recovery_orders = [o for o in orders if o["source"] == "RECOVERY"]
    ec3_targets = rng.sample(recovery_orders, min(30, len(recovery_orders)))
    second_camp = campaign_defs[1][0]  # CMP-2026-002
    for o in ec3_targets:
        # Find the original send for this order's cart
        orig_send = next(
            (s for s in sends if s["cart_id"] == o["cart_id"] and s["status"] == "DELIVERED"),
            None,
        )
        if orig_send is None:
            continue
        attr_counter += 1
        attributions.append(dict(
            attribution_id     = f"ATT-{YEAR}-{attr_counter:07d}",
            order_id           = o["order_id"],
            campaign_id        = second_camp,
            send_id            = orig_send["send_id"],
            attribution_type   = "FIRST_CLICK",
            attributed_revenue = o["total_amount"],
            issue_type         = "ATTRIBUTION_DOUBLE_COUNT",
        ))
        ec_counter += 1
        edge_cases.append(dict(
            edge_case_id      = f"EC-{ec_counter:06d}",
            issue_type        = "ATTRIBUTION_DOUBLE_COUNT",
            record_table      = "attributions",
            record_id         = attributions[-1]["attribution_id"],
            description       = "Order attributed to two campaigns simultaneously",
            expected_decision = "KEEP_LAST_CLICK_ONLY",
            created_at        = NOW,
        ))

    # --- EC4: Stale cart total (20 cases) ---
    # For 20 carts, corrupt total_amount by adding a fixed offset (simulate stale total)
    ec4_pool = [c for c in carts if c["issue_type"] is None]
    ec4_targets = rng.sample(ec4_pool, min(20, len(ec4_pool)))
    for c in ec4_targets:
        c["issue_type"]   = "STALE_CART_TOTAL"
        c["total_amount"] = c["total_amount"] + Decimal("9.99")   # stale by 9.99
        ec_counter += 1
        edge_cases.append(dict(
            edge_case_id      = f"EC-{ec_counter:06d}",
            issue_type        = "STALE_CART_TOTAL",
            record_table      = "carts",
            record_id         = c["cart_id"],
            description       = "cart.total_amount does not match sum of cart_lines extensions",
            expected_decision = "RECOMPUTE_FROM_LINES",
            created_at        = NOW,
        ))

    # -----------------------------------------------------------------------
    # STEP 3  Persist all rows (insertion order respects FK dependencies)
    # -----------------------------------------------------------------------
    with engine.begin() as conn:
        for table, rows in [
            (m.Customer.__table__,        customers),
            (m.Consent.__table__,         consents),
            (m.ConsentEvent.__table__,    consent_events),
            (m.Session.__table__,         sessions),
            (m.Cart.__table__,            carts),
            (m.CartLine.__table__,        cart_lines),
            (m.CartStatusEvent.__table__, cart_status_events),
            (m.Campaign.__table__,        campaigns),
            (m.MessageTemplate.__table__, templates),
            (m.FrequencyCapLog.__table__, freq_cap_logs),
            (m.SendAttempt.__table__,     sends),
            (m.Order.__table__,           orders),
            (m.Attribution.__table__,     attributions),
            (m.EdgeCase.__table__,        edge_cases),
        ]:
            if rows:
                _bulk_insert(conn, table, rows)

    # -----------------------------------------------------------------------
    # STEP 9  CSV export
    # -----------------------------------------------------------------------
    csv_checksums: dict[str, str] = {}
    if export_csv:
        OUTPUT_DIR.mkdir(exist_ok=True)
        exports = [
            (customers,          "customers.csv"),
            (consents,           "consents.csv"),
            (consent_events,     "consent_events.csv"),
            (sessions,           "sessions.csv"),
            (carts,              "carts.csv"),
            (cart_lines,         "cart_lines.csv"),
            (cart_status_events, "cart_status_events.csv"),
            (campaigns,          "campaigns.csv"),
            (templates,          "message_templates.csv"),
            (freq_cap_logs,      "frequency_cap_logs.csv"),
            (sends,              "send_attempts.csv"),
            (orders,             "orders.csv"),
            (attributions,       "attributions.csv"),
        ]
        for rows, fname in exports:
            path = _export_csv(rows, fname)
            csv_checksums[fname] = _sha256_file(path)

        # Ground-truth (separate file — never merged into input features)
        gt_path = OUTPUT_DIR / "ground_truth.csv"
        _export_csv(edge_cases, "ground_truth.csv")
        gt_json = OUTPUT_DIR / "ground_truth.json"
        gt_json.write_text(json.dumps(edge_cases, default=str, indent=2))

        # Source manifest
        manifest = {
            "generator":    "app/generate.py",
            "seed":          seed,
            "generated_at": NOW.isoformat(),
            "scale": {
                "customers": n_customers,
                "carts":     n_carts,
                "abandoned": n_abandoned,
                "sends":     n_sends,
            },
            "files": csv_checksums,
            "note": "All data is fully synthetic. Email domains use .test TLD.",
        }
        (OUTPUT_DIR / "source_manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )

    # -----------------------------------------------------------------------
    # STEP 10  Reproducibility self-check (row count comparison)
    # -----------------------------------------------------------------------
    summary = {
        "customers":          len(customers),
        "consents":           len(consents),
        "consent_events":     len(consent_events),
        "sessions":           len(sessions),
        "carts":              len(carts),
        "cart_lines":         len(cart_lines),
        "cart_status_events": len(cart_status_events),
        "campaigns":          len(campaigns),
        "message_templates":  len(templates),
        "frequency_cap_logs": len(freq_cap_logs),
        "send_attempts":      len(sends),
        "orders":             len(orders),
        "attributions":       len(attributions),
        "edge_cases":         len(edge_cases),
        "seed":               seed,
    }

    print("=" * 60)
    print("YottaFlex Cart Recovery — Generation Summary")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<24} {v:>10,}" if isinstance(v, int) else f"  {k:<24} {v}")
    print("=" * 60)

    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YottaFlex synthetic data generator")
    parser.add_argument("--small",      action="store_true", help="Smoke test: 100 carts")
    parser.add_argument("--keep-schema",action="store_true", help="Do not drop/recreate tables")
    parser.add_argument("--no-csv",     action="store_true", help="Skip CSV export")
    parser.add_argument("--seed",       type=int, default=SEED, help="Random seed")
    args = parser.parse_args()

    kw: dict = dict(seed=args.seed, reset=not args.keep_schema, export_csv=not args.no_csv)
    if args.small:
        kw.update(n_customers=50, n_carts=100, n_abandoned=40, n_sends=20)

    run(**kw)
