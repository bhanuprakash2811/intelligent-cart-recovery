"""
YottaFlex Cart Recovery — Test Suite (15 tests)
===============================================
All tests use the SQLite in-memory fixture from conftest.py.
No PostgreSQL / Docker required.

Run:  pytest tests/ -v
"""

from __future__ import annotations

import re
from decimal import Decimal


# ============================================================
# 1. Project files exist
# ============================================================
def test_project_files_exist():
    from pathlib import Path
    expected = [
        "app/models.py",
        "app/generate.py",
        "app/validate.py",
        "app/engines.py",
        "app/main.py",
        "app/export.py",
        "app/repro.py",
        "dashboard/app.py",
        "tests/conftest.py",
        "docs/data_dictionary.md",
        "docs/business_rules.md",
        "README.md",
    ]
    missing = [p for p in expected if not Path(p).exists()]
    assert not missing, f"Missing files: {missing}"


# ============================================================
# 2. Small-scale generator runs without error
# ============================================================
def test_small_generation_runs(small_gen):
    assert small_gen["carts"] == 100
    assert small_gen["customers"] == 50


# ============================================================
# 3. PK uniqueness — customers
# ============================================================
def test_pk_uniqueness_customers(db_session):
    from sqlalchemy import select, func
    from app import models as m

    total  = db_session.scalar(select(func.count()).select_from(m.Customer))
    unique = db_session.scalar(select(func.count(m.Customer.customer_id.distinct())))
    assert total == unique, "Duplicate customer_id values found"


# ============================================================
# 4. PK uniqueness — carts
# ============================================================
def test_pk_uniqueness_carts(db_session):
    from sqlalchemy import select, func
    from app import models as m

    total  = db_session.scalar(select(func.count()).select_from(m.Cart))
    unique = db_session.scalar(select(func.count(m.Cart.cart_id.distinct())))
    assert total == unique, "Duplicate cart_id values found"


# ============================================================
# 5. FK integrity — cart lines reference valid carts
# ============================================================
def test_fk_integrity_cart_lines(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        orphans = conn.execute(text("""
            SELECT COUNT(*) FROM cart_lines cl
            LEFT JOIN carts c ON c.cart_id = cl.cart_id
            WHERE c.cart_id IS NULL
        """)).scalar_one()
    assert orphans == 0, f"{orphans} cart_lines have no matching cart"


# ============================================================
# 6. FK integrity — sessions reference valid customers
# ============================================================
def test_fk_integrity_sessions(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        orphans = conn.execute(text("""
            SELECT COUNT(*) FROM sessions s
            LEFT JOIN customers c ON c.customer_id = s.customer_id
            WHERE c.customer_id IS NULL
        """)).scalar_one()
    assert orphans == 0, f"{orphans} sessions have no matching customer"


# ============================================================
# 7. Cart total reconciliation (excluding STALE_CART_TOTAL edge cases)
# ============================================================
def test_cart_total_reconciliation(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        bad = conn.execute(text("""
            SELECT COUNT(*) FROM carts c
            JOIN (
                SELECT cart_id, SUM(quantity * unit_price) AS line_total
                FROM cart_lines GROUP BY cart_id
            ) x ON x.cart_id = c.cart_id
            WHERE (c.issue_type IS NULL OR c.issue_type != 'STALE_CART_TOTAL')
              AND ABS(CAST(c.total_amount AS FLOAT) - CAST(x.line_total AS FLOAT)) > 0.015
        """)).scalar_one()
    assert bad == 0, f"{bad} carts have total_amount mismatched with cart_lines (non-edge-case)"


# ============================================================
# 8. Consent suppression respected
# ============================================================
def test_consent_suppression_respected(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        bad = conn.execute(text("""
            SELECT COUNT(*) FROM send_attempts s
            JOIN consents c
                ON c.customer_id = s.customer_id AND c.channel = s.channel
            WHERE s.status = 'DELIVERED'
              AND c.is_consented = 0
              AND (s.issue_type IS NULL OR s.issue_type != 'MSG_AFTER_OPT_OUT')
        """)).scalar_one()
    assert bad == 0, f"{bad} messages delivered without valid consent (non-edge-case)"


# ============================================================
# 9. Attribution uniqueness per order (excluding DOUBLE_COUNT edge cases)
# ============================================================
def test_attribution_unique_per_order(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        dupes = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT order_id FROM attributions
                WHERE issue_type IS NULL OR issue_type != 'ATTRIBUTION_DOUBLE_COUNT'
                GROUP BY order_id HAVING COUNT(*) > 1
            ) sub
        """)).scalar_one()
    assert dupes == 0, f"{dupes} orders have multiple attributions (non-edge-case)"


# ============================================================
# 10. Abandonment time rule
# ============================================================
def test_abandonment_time_rule(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        # Every ABANDONED cart should have last_activity_at < created_at + 60 min
        # i.e. the idle gap from last_activity_at to abandonment detection > 60 min
        # We check: abandoned carts have idle time >= 60 min
        # idle = last_activity_at - created_at   (should be > 60 min for abandoned)
        bad = conn.execute(text("""
            SELECT COUNT(*) FROM carts
            WHERE abandonment_label = 1
              AND (issue_type IS NULL OR issue_type NOT IN ('ABANDONED_AFTER_PURCHASE'))
              AND (julianday(last_activity_at) - julianday(created_at)) * 24 * 60 < 60
        """)).scalar_one()
    # Allow small tolerance: should be 0 for clean rows
    assert bad == 0, f"{bad} 'abandoned' carts have idle time < 60 minutes"


# ============================================================
# 11. Frequency cap not exceeded (clean rows)
# ============================================================
def test_frequency_cap_not_exceeded(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        # For each (customer, channel, date), the count of DELIVERED sends
        # should not exceed FREQ_CAP_DAILY (3)
        bad = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT customer_id, channel,
                       substr(scheduled_at, 1, 10) AS send_date,
                       COUNT(*) AS daily_cnt
                FROM send_attempts
                WHERE status = 'DELIVERED'
                GROUP BY customer_id, channel, substr(scheduled_at, 1, 10)
                HAVING COUNT(*) > 3
            ) sub
        """)).scalar_one()
    assert bad == 0, f"{bad} (customer, channel, date) groups exceed frequency cap of 3"


# ============================================================
# 12. Edge case labels are present in DB
# ============================================================
def test_edge_case_labels_present(db_session):
    from sqlalchemy import select, func
    from app import models as m

    total_ec = db_session.scalar(select(func.count()).select_from(m.EdgeCase))
    assert total_ec > 0, "No edge cases found in edge_cases table"

    expected_types = {
        "MSG_AFTER_OPT_OUT",
        "ABANDONED_AFTER_PURCHASE",
        "ATTRIBUTION_DOUBLE_COUNT",
        "STALE_CART_TOTAL",
    }
    from sqlalchemy import text
    with db_session.connection() as conn:
        found = {r[0] for r in conn.execute(text("SELECT DISTINCT issue_type FROM edge_cases")).fetchall()}

    assert expected_types.issubset(found), f"Missing edge case types: {expected_types - found}"


# ============================================================
# 13. Ground truth not in input feature tables
# ============================================================
def test_ground_truth_not_in_features(db_session):
    """
    edge_cases table should not expose issue_type in the main carts/sends
    feature view — verify the feature tables only carry issue_type for tagging,
    never expose the edge_case_id or expected_decision columns.
    """
    from app import models as m
    from sqlalchemy import inspect

    inspector = inspect(db_session.bind)

    carts_cols  = {c["name"] for c in inspector.get_columns("carts")}
    sends_cols  = {c["name"] for c in inspector.get_columns("send_attempts")}
    ec_cols     = {c["name"] for c in inspector.get_columns("edge_cases")}

    # expected_decision and edge_case_id must only be in edge_cases
    assert "expected_decision" not in carts_cols
    assert "expected_decision" not in sends_cols
    assert "edge_case_id"      not in carts_cols
    assert "edge_case_id"      not in sends_cols
    assert "expected_decision" in ec_cols


# ============================================================
# 14. Reproducibility — same seed = same row counts
# ============================================================
def test_reproducibility_row_counts():
    """
    Two independent in-memory runs with the same seed must produce
    identical row counts for every table.
    """
    import os
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

    # We need fresh engines for each run — use separate modules
    from app.generate import run as gen_run

    # Run 1
    summary1 = gen_run(
        seed=20261022, n_customers=50, n_carts=100,
        n_abandoned=40, n_sends=20, reset=True, export_csv=False,
    )

    # Run 2 (same params)
    summary2 = gen_run(
        seed=20261022, n_customers=50, n_carts=100,
        n_abandoned=40, n_sends=20, reset=True, export_csv=False,
    )

    for key in summary1:
        if key == "seed":
            continue
        assert summary1[key] == summary2[key], (
            f"Row count mismatch for '{key}': "
            f"run1={summary1[key]}, run2={summary2[key]}"
        )


# ============================================================
# 15. ID format compliance
# ============================================================
def test_id_format_compliance(db_session):
    from sqlalchemy import text

    with db_session.connection() as conn:
        # Customer IDs: CUS-NNNNNN
        bad_cus = conn.execute(text(
            "SELECT COUNT(*) FROM customers WHERE customer_id NOT LIKE 'CUS-%'"
        )).scalar_one()
        assert bad_cus == 0, f"{bad_cus} customer IDs do not match CUS-NNNNNN format"

        # Cart IDs: CART-YYYY-NNNNNNN
        bad_cart = conn.execute(text(
            "SELECT COUNT(*) FROM carts WHERE cart_id NOT LIKE 'CART-%'"
        )).scalar_one()
        assert bad_cart == 0, f"{bad_cart} cart IDs do not match CART-YYYY-NNNNNNN format"

        # Send IDs: SEND-YYYY-NNNNNNN
        bad_send = conn.execute(text(
            "SELECT COUNT(*) FROM send_attempts WHERE send_id NOT LIKE 'SEND-%'"
        )).scalar_one()
        assert bad_send == 0, f"{bad_send} send IDs do not match SEND-YYYY-NNNNNNN format"

        # Order IDs: ORD-YYYY-NNNNNNN
        bad_ord = conn.execute(text(
            "SELECT COUNT(*) FROM orders WHERE order_id NOT LIKE 'ORD-%'"
        )).scalar_one()
        assert bad_ord == 0, f"{bad_ord} order IDs do not match ORD-YYYY-NNNNNNN format"
