"""
YottaFlex Cart Recovery — FastAPI Application
=============================================
Endpoints:
  GET  /health
  GET  /api/v1/analytics/overview
  GET  /api/v1/analytics/funnel
  GET  /api/v1/analytics/campaigns
  GET  /api/v1/analytics/consent
  GET  /api/v1/analytics/recovery-by-day
  GET  /api/v1/analytics/top-products
  GET  /api/v1/carts/abandoned
  GET  /api/v1/sends
  GET  /api/v1/validation/report
  GET  /api/v1/ground-truth/anomalies
  POST /api/v1/generate
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, text

from .db import SessionLocal, Base, engine
from . import models as m
from .engines import (
    summary,
    funnel_summary,
    campaign_performance,
    consent_breakdown,
    recovery_rate_by_day,
    top_abandoned_products,
    frequency_cap_summary,
)
from .validate import validate

app = FastAPI(
    title="YottaFlex Cart Recovery API",
    version="1.0.0",
    description=(
        "Synthetic e-commerce abandoned-cart recovery and attribution platform. "
        "All data is generated from a fixed seed and is not real customer data."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health():
    """Liveness probe."""
    return {"status": "ok", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@app.get("/api/v1/analytics/overview", tags=["Analytics"])
def overview(db: Session = Depends(get_db)):
    """High-level KPI counts: customers, carts, sends, revenue, etc."""
    return summary(db)


@app.get("/api/v1/analytics/funnel", tags=["Analytics"])
def funnel(db: Session = Depends(get_db)):
    """
    Conversion funnel:
    total carts → abandoned → sent → delivered → opened → clicked → converted.
    Includes percentage at each stage.
    """
    return funnel_summary(db)


@app.get("/api/v1/analytics/campaigns", tags=["Analytics"])
def campaigns(db: Session = Depends(get_db)):
    """Per-campaign performance: delivery rate, open rate, click rate, conversion rate, revenue."""
    return campaign_performance(db)


@app.get("/api/v1/analytics/consent", tags=["Analytics"])
def consent(db: Session = Depends(get_db)):
    """Opt-in / opt-out breakdown per channel (EMAIL, SMS)."""
    return consent_breakdown(db)


@app.get("/api/v1/analytics/recovery-by-day", tags=["Analytics"])
def recovery_by_day(db: Session = Depends(get_db)):
    """Daily abandoned cart count vs recovery order count."""
    return recovery_rate_by_day(db)


@app.get("/api/v1/analytics/top-products", tags=["Analytics"])
def top_products(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Top products by value sitting in abandoned carts."""
    return top_abandoned_products(db, limit=limit)


@app.get("/api/v1/analytics/frequency-cap", tags=["Analytics"])
def freq_cap(db: Session = Depends(get_db)):
    """Summary of frequency-cap enforcement: total capped sends and affected customers."""
    return frequency_cap_summary(db)


# ---------------------------------------------------------------------------
# Carts
# ---------------------------------------------------------------------------

@app.get("/api/v1/carts/abandoned", tags=["Carts"])
def abandoned_carts(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Paginated list of carts with abandonment_label=TRUE."""
    rows = db.scalars(
        select(m.Cart)
        .where(m.Cart.abandonment_label.is_(True))
        .order_by(m.Cart.last_activity_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "cart_id":          r.cart_id,
            "customer_id":      r.customer_id,
            "session_id":       r.session_id,
            "status":           r.status,
            "total_amount":     float(r.total_amount),
            "last_activity_at": r.last_activity_at.isoformat() if r.last_activity_at else None,
            "issue_type":       r.issue_type,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Send Attempts
# ---------------------------------------------------------------------------

@app.get("/api/v1/sends", tags=["Sends"])
def send_attempts(
    status: Optional[str] = Query(None, description="DELIVERED | SUPPRESSED | FREQ_CAP | BOUNCED"),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Paginated send attempts, optionally filtered by status."""
    q = select(m.SendAttempt).order_by(m.SendAttempt.scheduled_at.desc())
    if status:
        q = q.where(m.SendAttempt.status == status.upper())
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return [
        {
            "send_id":      r.send_id,
            "campaign_id":  r.campaign_id,
            "customer_id":  r.customer_id,
            "cart_id":      r.cart_id,
            "channel":      r.channel,
            "status":       r.status,
            "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
            "sent_at":      r.sent_at.isoformat() if r.sent_at else None,
            "opened_at":    r.opened_at.isoformat() if r.opened_at else None,
            "clicked_at":   r.clicked_at.isoformat() if r.clicked_at else None,
            "issue_type":   r.issue_type,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@app.get("/api/v1/validation/report", tags=["Validation"])
def validation_report():
    """
    Run all 8 validation checks and return a structured report.
    status = PASS | ISSUES_FOUND
    """
    return validate()


# ---------------------------------------------------------------------------
# Ground Truth
# ---------------------------------------------------------------------------

@app.get("/api/v1/ground-truth/anomalies", tags=["Ground Truth"])
def ground_truth_anomalies(
    issue_type: Optional[str] = Query(None),
    limit:  int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Return labelled edge-case / anomaly records.
    These are strictly separate from input features.
    issue_type filter: MSG_AFTER_OPT_OUT | ABANDONED_AFTER_PURCHASE |
                       ATTRIBUTION_DOUBLE_COUNT | STALE_CART_TOTAL
    """
    q = select(m.EdgeCase).order_by(m.EdgeCase.edge_case_id)
    if issue_type:
        q = q.where(m.EdgeCase.issue_type == issue_type.upper())
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return [
        {
            "edge_case_id":      r.edge_case_id,
            "issue_type":        r.issue_type,
            "record_table":      r.record_table,
            "record_id":         r.record_id,
            "description":       r.description,
            "expected_decision": r.expected_decision,
            "created_at":        r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Generator trigger (useful for notebooks / CI)
# ---------------------------------------------------------------------------

@app.post("/api/v1/generate", tags=["System"])
def trigger_generate(
    small: bool = Query(False, description="Use --small scale (100 carts)"),
    seed:  int  = Query(20261022, description="Random seed"),
):
    """
    Trigger the synthetic data generator.
    WARNING: This drops and recreates all tables.
    """
    from .generate import run as _run
    kw: dict = dict(seed=seed, reset=True, export_csv=True)
    if small:
        kw.update(n_customers=50, n_carts=100, n_abandoned=40, n_sends=20)
    result = _run(**kw)
    return {"status": "ok", "summary": result}
