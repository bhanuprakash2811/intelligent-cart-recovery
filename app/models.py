"""
SQLAlchemy ORM models for YottaFlex Cart Recovery.

Tables
------
Customer         – master customer profile
Consent          – latest consent state per channel (materialized)
ConsentEvent     – immutable consent history log
Session          – browser/app session
Cart             – cart lifecycle + totals
CartLine         – line-item rows (source of truth for arithmetic)
CartStatusEvent  – immutable cart state-transition log
Campaign         – recovery campaign configuration
MessageTemplate  – campaign message template
FrequencyCapLog  – per-customer daily send counts (enforces cap)
SendAttempt      – every scheduled/sent/suppressed message
Order            – completed purchase
Attribution      – campaign-to-order credit
EdgeCase         – labelled anomaly / ground-truth record
"""

from sqlalchemy import (
    String, Integer, Boolean, DateTime, Numeric, ForeignKey, Text,
    UniqueConstraint, Index, CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


# ---------------------------------------------------------------------------
# Master / parent tables
# ---------------------------------------------------------------------------

class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    full_name:   Mapped[str] = mapped_column(String(100), nullable=False)
    email:       Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    phone:       Mapped[str] = mapped_column(String(20), nullable=False)
    created_at:  Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("customer_id LIKE 'CUS-%'", name="ck_customer_id_prefix"),
    )


class Consent(Base):
    """Materialised latest-state per (customer, channel). Updated on each opt-in/out."""
    __tablename__ = "consents"

    consent_id:   Mapped[str]  = mapped_column(String(40), primary_key=True)
    customer_id:  Mapped[str]  = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    channel:      Mapped[str]  = mapped_column(String(10), nullable=False)   # EMAIL | SMS
    is_consented: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    source:       Mapped[str]  = mapped_column(String(30), nullable=False)   # SIGNUP | WITHDRAWAL | REOPT_IN

    __table_args__ = (
        UniqueConstraint("customer_id", "channel", name="uq_consent_customer_channel"),
        CheckConstraint("channel IN ('EMAIL','SMS')", name="ck_consent_channel"),
    )


class ConsentEvent(Base):
    """Immutable consent history.  Never update; append only."""
    __tablename__ = "consent_events"

    event_id:     Mapped[str]  = mapped_column(String(40), primary_key=True)
    customer_id:  Mapped[str]  = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    channel:      Mapped[str]  = mapped_column(String(10), nullable=False)
    event_type:   Mapped[str]  = mapped_column(String(20), nullable=False)   # OPT_IN | OPT_OUT
    occurred_at:  Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    source:       Mapped[str]  = mapped_column(String(30), nullable=False)

    __table_args__ = (
        CheckConstraint("event_type IN ('OPT_IN','OPT_OUT')", name="ck_consent_event_type"),
    )


class Session(Base):
    __tablename__ = "sessions"

    session_id:       Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id:      Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    device_type:      Mapped[str] = mapped_column(String(20), nullable=False)  # DESKTOP | MOBILE | TABLET
    started_at:       Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("device_type IN ('DESKTOP','MOBILE','TABLET')", name="ck_session_device"),
    )


# ---------------------------------------------------------------------------
# Cart entities
# ---------------------------------------------------------------------------

class Cart(Base):
    __tablename__ = "carts"

    cart_id:          Mapped[str]   = mapped_column(String(40), primary_key=True)
    customer_id:      Mapped[str]   = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    session_id:       Mapped[str]   = mapped_column(ForeignKey("sessions.session_id"), nullable=False)
    status:           Mapped[str]   = mapped_column(String(20), nullable=False)   # ACTIVE | ABANDONED | RECOVERED | PURCHASED
    created_at:       Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    total_amount:     Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    abandonment_label: Mapped[bool] = mapped_column(Boolean, nullable=False)
    issue_type:       Mapped[str | None] = mapped_column(String(60))   # NULL for clean rows

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','ABANDONED','RECOVERED','PURCHASED')", name="ck_cart_status"),
        CheckConstraint("total_amount >= 0", name="ck_cart_total_positive"),
    )


class CartLine(Base):
    __tablename__ = "cart_lines"

    cart_line_id: Mapped[str]   = mapped_column(String(40), primary_key=True)
    cart_id:      Mapped[str]   = mapped_column(ForeignKey("carts.cart_id"), nullable=False)
    product_id:   Mapped[str]   = mapped_column(String(32), nullable=False)
    product_name: Mapped[str]   = mapped_column(String(100), nullable=False)
    quantity:     Mapped[int]   = mapped_column(Integer, nullable=False)
    unit_price:   Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_line_qty_positive"),
        CheckConstraint("unit_price > 0", name="ck_line_price_positive"),
    )


class CartStatusEvent(Base):
    """Immutable cart lifecycle transition log."""
    __tablename__ = "cart_status_events"

    event_id:    Mapped[str] = mapped_column(String(40), primary_key=True)
    cart_id:     Mapped[str] = mapped_column(ForeignKey("carts.cart_id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status:   Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    reason:      Mapped[str | None] = mapped_column(String(80))


# ---------------------------------------------------------------------------
# Campaign entities
# ---------------------------------------------------------------------------

class Campaign(Base):
    __tablename__ = "campaigns"

    campaign_id:    Mapped[str]  = mapped_column(String(32), primary_key=True)
    name:           Mapped[str]  = mapped_column(String(100), nullable=False)
    channel:        Mapped[str]  = mapped_column(String(10), nullable=False)
    delay_minutes:  Mapped[int]  = mapped_column(Integer, nullable=False)
    active:         Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("channel IN ('EMAIL','SMS')", name="ck_campaign_channel"),
        CheckConstraint("delay_minutes >= 0", name="ck_campaign_delay"),
    )


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    template_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.campaign_id"), nullable=False)
    subject:     Mapped[str] = mapped_column(String(160), nullable=False)
    body:        Mapped[str] = mapped_column(Text, nullable=False)


class FrequencyCapLog(Base):
    """Tracks how many messages were sent to a customer on a given UTC day."""
    __tablename__ = "frequency_cap_logs"

    cap_log_id:   Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id:  Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    channel:      Mapped[str] = mapped_column(String(10), nullable=False)
    send_date:    Mapped[str] = mapped_column(String(10), nullable=False)   # YYYY-MM-DD
    send_count:   Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("customer_id", "channel", "send_date", name="uq_cap_customer_channel_date"),
    )


class SendAttempt(Base):
    __tablename__ = "send_attempts"

    send_id:      Mapped[str] = mapped_column(String(40), primary_key=True)
    campaign_id:  Mapped[str] = mapped_column(ForeignKey("campaigns.campaign_id"), nullable=False)
    template_id:  Mapped[str] = mapped_column(ForeignKey("message_templates.template_id"), nullable=False)
    customer_id:  Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    cart_id:      Mapped[str] = mapped_column(ForeignKey("carts.cart_id"), nullable=False)
    channel:      Mapped[str] = mapped_column(String(10), nullable=False)
    status:       Mapped[str] = mapped_column(String(20), nullable=False)
    # status values: DELIVERED | SUPPRESSED | FREQ_CAP | BOUNCED
    scheduled_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at:      Mapped[object | None] = mapped_column(DateTime(timezone=True))
    opened_at:    Mapped[object | None] = mapped_column(DateTime(timezone=True))
    clicked_at:   Mapped[object | None] = mapped_column(DateTime(timezone=True))
    issue_type:   Mapped[str | None] = mapped_column(String(60))   # NULL for clean rows

    __table_args__ = (
        CheckConstraint("channel IN ('EMAIL','SMS')", name="ck_send_channel"),
        CheckConstraint("status IN ('DELIVERED','SUPPRESSED','FREQ_CAP','BOUNCED')", name="ck_send_status"),
    )


# ---------------------------------------------------------------------------
# Order and attribution
# ---------------------------------------------------------------------------

class Order(Base):
    __tablename__ = "orders"

    order_id:     Mapped[str]   = mapped_column(String(40), primary_key=True)
    customer_id:  Mapped[str]   = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    cart_id:      Mapped[str]   = mapped_column(ForeignKey("carts.cart_id"), nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    ordered_at:   Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    source:       Mapped[str]   = mapped_column(String(20), nullable=False)
    # source: DIRECT | RECOVERY | LATE_PURCHASE

    __table_args__ = (
        CheckConstraint("source IN ('DIRECT','RECOVERY','LATE_PURCHASE')", name="ck_order_source"),
        CheckConstraint("total_amount > 0", name="ck_order_total_positive"),
    )


class Attribution(Base):
    __tablename__ = "attributions"

    attribution_id:    Mapped[str]   = mapped_column(String(40), primary_key=True)
    order_id:          Mapped[str]   = mapped_column(ForeignKey("orders.order_id"), nullable=False)
    campaign_id:       Mapped[str]   = mapped_column(ForeignKey("campaigns.campaign_id"), nullable=False)
    send_id:           Mapped[str]   = mapped_column(ForeignKey("send_attempts.send_id"), nullable=False)
    attribution_type:  Mapped[str]   = mapped_column(String(20), nullable=False, default="LAST_CLICK")
    attributed_revenue: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    issue_type:        Mapped[str | None] = mapped_column(String(60))   # NULL = clean; DOUBLE_COUNT = edge case

    __table_args__ = (
        CheckConstraint("attribution_type IN ('LAST_CLICK','FIRST_CLICK','LINEAR')", name="ck_attr_type"),
    )


# ---------------------------------------------------------------------------
# Ground-truth / edge-case table (never merged into input features)
# ---------------------------------------------------------------------------

class EdgeCase(Base):
    """
    Labelled anomalies injected for evaluation.  Kept strictly separate;
    never joined into feature queries.
    """
    __tablename__ = "edge_cases"

    edge_case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    issue_type:   Mapped[str] = mapped_column(String(60), nullable=False)
    # MSG_AFTER_OPT_OUT | ABANDONED_AFTER_PURCHASE |
    # ATTRIBUTION_DOUBLE_COUNT | STALE_CART_TOTAL
    record_table: Mapped[str] = mapped_column(String(40), nullable=False)
    record_id:    Mapped[str] = mapped_column(String(40), nullable=False)
    description:  Mapped[str] = mapped_column(Text, nullable=False)
    expected_decision: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at:   Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

Index("ix_carts_status_activity", Cart.status, Cart.last_activity_at)
Index("ix_carts_customer",        Cart.customer_id)
Index("ix_sends_customer_channel", SendAttempt.customer_id, SendAttempt.channel)
Index("ix_sends_cart",            SendAttempt.cart_id)
Index("ix_consent_events_customer", ConsentEvent.customer_id, ConsentEvent.channel)
Index("ix_orders_cart",           Order.cart_id)
Index("ix_attributions_order",    Attribution.order_id)
