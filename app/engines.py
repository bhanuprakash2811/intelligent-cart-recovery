"""
YottaFlex Cart Recovery — Analytics Engine
==========================================
Business-logic query functions used by the FastAPI layer and dashboard.
All functions accept a SQLAlchemy Session object.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from . import models as m

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
ABANDONMENT_MINUTES = 60


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def detect_abandoned_carts(db: Session, threshold_minutes: int = ABANDONMENT_MINUTES):
    """Return Cart rows whose last_activity_at exceeds threshold and status=ACTIVE."""
    cutoff = NOW - timedelta(minutes=threshold_minutes)
    return db.scalars(
        select(m.Cart).where(
            m.Cart.status == "ACTIVE",
            m.Cart.last_activity_at <= cutoff,
        )
    ).all()


def latest_consent(db: Session, customer_id: str, channel: str):
    """Fetch the most recent Consent row for a customer+channel."""
    return db.scalar(
        select(m.Consent)
        .where(m.Consent.customer_id == customer_id, m.Consent.channel == channel)
        .order_by(m.Consent.effective_at.desc())
        .limit(1)
    )


def consent_decision(db: Session, customer_id: str, channel: str) -> tuple[bool, str]:
    row = latest_consent(db, customer_id, channel)
    if row is None:
        return False, "NO_CONSENT_RECORD"
    if not row.is_consented:
        return False, "OPTED_OUT"
    return True, "CONSENT_VALID"


# ---------------------------------------------------------------------------
# Attribution helper
# ---------------------------------------------------------------------------

def attribute_order(db: Session, order_id: str, campaign_id: str, send_id: str, revenue: float):
    """Create a single LAST_CLICK attribution. DB unique constraint enforces 1 per order."""
    record = m.Attribution(
        attribution_id     = f"ATT-MANUAL-{order_id}",
        order_id           = order_id,
        campaign_id        = campaign_id,
        send_id            = send_id,
        attribution_type   = "LAST_CLICK",
        attributed_revenue = revenue,
        issue_type         = None,
    )
    db.add(record)
    db.commit()
    return record


# ---------------------------------------------------------------------------
# Dashboard / API query functions
# ---------------------------------------------------------------------------

def summary(db: Session) -> dict:
    """High-level KPI counts."""
    return {
        "customers":       db.scalar(select(func.count()).select_from(m.Customer)),
        "carts":           db.scalar(select(func.count()).select_from(m.Cart)),
        "abandoned_carts": db.scalar(select(func.count()).select_from(m.Cart)
                                     .where(m.Cart.abandonment_label.is_(True))),
        "active_carts":    db.scalar(select(func.count()).select_from(m.Cart)
                                     .where(m.Cart.status == "ACTIVE")),
        "recovered_carts": db.scalar(select(func.count()).select_from(m.Cart)
                                     .where(m.Cart.status == "RECOVERED")),
        "send_attempts":   db.scalar(select(func.count()).select_from(m.SendAttempt)),
        "delivered":       db.scalar(select(func.count()).select_from(m.SendAttempt)
                                     .where(m.SendAttempt.status == "DELIVERED")),
        "suppressed":      db.scalar(select(func.count()).select_from(m.SendAttempt)
                                     .where(m.SendAttempt.status == "SUPPRESSED")),
        "freq_capped":     db.scalar(select(func.count()).select_from(m.SendAttempt)
                                     .where(m.SendAttempt.status == "FREQ_CAP")),
        "orders":          db.scalar(select(func.count()).select_from(m.Order)),
        "recovery_orders": db.scalar(select(func.count()).select_from(m.Order)
                                     .where(m.Order.source == "RECOVERY")),
        "attributions":    db.scalar(select(func.count()).select_from(m.Attribution)),
        "attributed_revenue": float(db.scalar(
            select(func.coalesce(func.sum(m.Attribution.attributed_revenue), 0))
            .where(m.Attribution.issue_type.is_(None))
        ) or 0),
    }


def funnel_summary(db: Session) -> dict:
    """Conversion funnel: carts → abandoned → sent → delivered → opened → clicked → converted."""
    total_carts    = db.scalar(select(func.count()).select_from(m.Cart))
    abandoned      = db.scalar(select(func.count()).select_from(m.Cart)
                               .where(m.Cart.abandonment_label.is_(True)))
    sent           = db.scalar(select(func.count()).select_from(m.SendAttempt))
    delivered      = db.scalar(select(func.count()).select_from(m.SendAttempt)
                               .where(m.SendAttempt.status == "DELIVERED"))
    opened         = db.scalar(select(func.count()).select_from(m.SendAttempt)
                               .where(m.SendAttempt.opened_at.is_not(None)))
    clicked        = db.scalar(select(func.count()).select_from(m.SendAttempt)
                               .where(m.SendAttempt.clicked_at.is_not(None)))
    converted      = db.scalar(select(func.count()).select_from(m.Order)
                               .where(m.Order.source == "RECOVERY"))

    def pct(a, b):
        return round(a / b * 100, 2) if b else 0

    return {
        "total_carts":      total_carts,
        "abandoned":        abandoned,
        "pct_abandoned":    pct(abandoned, total_carts),
        "sent":             sent,
        "delivered":        delivered,
        "pct_delivered":    pct(delivered, sent),
        "opened":           opened,
        "pct_opened":       pct(opened, delivered),
        "clicked":          clicked,
        "pct_clicked":      pct(clicked, opened),
        "converted":        converted,
        "pct_converted":    pct(converted, clicked),
        "overall_recovery": pct(converted, abandoned),
    }


def campaign_performance(db: Session) -> list[dict]:
    """Per-campaign: sent, delivered, clicked, converted, attributed revenue."""
    with db.connection() as conn:
        rows = conn.execute(text("""
            SELECT
                c.campaign_id,
                c.name,
                c.channel,
                c.delay_minutes,
                COUNT(s.send_id)                                      AS sent,
                SUM(CASE WHEN s.status='DELIVERED'  THEN 1 ELSE 0 END) AS delivered,
                SUM(CASE WHEN s.opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened,
                SUM(CASE WHEN s.clicked_at IS NOT NULL THEN 1 ELSE 0 END) AS clicked,
                COUNT(DISTINCT o.order_id)                            AS converted,
                COALESCE(SUM(CASE WHEN a.issue_type IS NULL
                             THEN CAST(a.attributed_revenue AS FLOAT) ELSE 0 END), 0)
                                                                       AS attributed_revenue
            FROM campaigns c
            LEFT JOIN send_attempts s   ON s.campaign_id = c.campaign_id
            LEFT JOIN attributions  a   ON a.campaign_id = c.campaign_id
                                       AND a.issue_type IS NULL
            LEFT JOIN orders        o   ON o.order_id    = a.order_id
            GROUP BY c.campaign_id, c.name, c.channel, c.delay_minutes
            ORDER BY c.campaign_id
        """)).fetchall()
    return [
        {
            "campaign_id":        r[0],
            "name":               r[1],
            "channel":            r[2],
            "delay_minutes":      r[3],
            "sent":               r[4],
            "delivered":          r[5],
            "opened":             r[6],
            "clicked":            r[7],
            "converted":          r[8],
            "attributed_revenue": round(float(r[9]), 2),
            "delivery_rate":      round(r[5] / r[4] * 100, 2) if r[4] else 0,
            "open_rate":          round(r[6] / r[5] * 100, 2) if r[5] else 0,
            "click_rate":         round(r[7] / r[6] * 100, 2) if r[6] else 0,
            "conversion_rate":    round(r[8] / r[7] * 100, 2) if r[7] else 0,
        }
        for r in rows
    ]


def consent_breakdown(db: Session) -> dict:
    """Opt-in / opt-out counts per channel."""
    with db.connection() as conn:
        rows = conn.execute(text("""
            SELECT channel,
                   SUM(CASE WHEN is_consented THEN 1 ELSE 0 END) AS opted_in,
                   SUM(CASE WHEN NOT is_consented THEN 1 ELSE 0 END) AS opted_out
            FROM consents
            GROUP BY channel
        """)).fetchall()
    return {r[0]: {"opted_in": r[1], "opted_out": r[2]} for r in rows}


def recovery_rate_by_day(db: Session) -> list[dict]:
    """Daily abandoned carts vs recovery orders."""
    with db.connection() as conn:
        rows = conn.execute(text("""
            SELECT
                DATE(c.last_activity_at) AS day,
                COUNT(c.cart_id)         AS abandoned,
                COUNT(o.order_id)        AS recovered
            FROM carts c
            LEFT JOIN orders o
                ON o.cart_id = c.cart_id AND o.source = 'RECOVERY'
            WHERE c.abandonment_label = TRUE
            GROUP BY DATE(c.last_activity_at)
            ORDER BY day
        """)).fetchall()
    return [
        {
            "day":       str(r[0]),
            "abandoned": r[1],
            "recovered": r[2],
            "rate":      round(r[2] / r[1] * 100, 2) if r[1] else 0,
        }
        for r in rows
    ]


def top_abandoned_products(db: Session, limit: int = 10) -> list[dict]:
    """Top products by value sitting in abandoned carts."""
    with db.connection() as conn:
        rows = conn.execute(text(f"""
            SELECT
                cl.product_id,
                cl.product_name,
                SUM(cl.quantity)                               AS total_qty,
                SUM(cl.quantity * cl.unit_price)               AS total_value,
                COUNT(DISTINCT cl.cart_id)                     AS cart_count
            FROM cart_lines cl
            JOIN carts c ON c.cart_id = cl.cart_id
            WHERE c.abandonment_label = TRUE
            GROUP BY cl.product_id, cl.product_name
            ORDER BY total_value DESC
            LIMIT {int(limit)}
        """)).fetchall()   # noqa: S608
    return [
        {
            "product_id":   r[0],
            "product_name": r[1],
            "total_qty":    r[2],
            "total_value":  round(float(r[3]), 2),
            "cart_count":   r[4],
        }
        for r in rows
    ]


def frequency_cap_summary(db: Session) -> dict:
    """How many customers hit the daily frequency cap."""
    with db.connection() as conn:
        capped = conn.execute(text("""
            SELECT COUNT(*) FROM send_attempts WHERE status = 'FREQ_CAP'
        """)).scalar_one()
        cap_customers = conn.execute(text("""
            SELECT COUNT(DISTINCT customer_id) FROM send_attempts WHERE status = 'FREQ_CAP'
        """)).scalar_one()
    return {"freq_capped_sends": capped, "affected_customers": cap_customers}
