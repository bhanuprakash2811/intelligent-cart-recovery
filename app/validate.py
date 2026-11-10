"""
YottaFlex Cart Recovery — Validation Engine
===========================================
8 deterministic validation checks:
  1. PK uniqueness per table
  2. FK integrity (orphaned child rows)
  3. Enum value enforcement
  4. Date ordering constraints
  5. Arithmetic reconciliation (cart total vs line extensions)
  6. Lifecycle transition validity
  7. Duplicate attribution (excluding labelled DOUBLE_COUNT edge cases)
  8. Consent enforcement (no DELIVERED send where latest consent = FALSE,
     excluding labelled MSG_AFTER_OPT_OUT edge cases)

Returns a structured dict with status, counts, and issue details.
"""

from sqlalchemy import text
from .db import engine


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_pk_uniqueness(conn) -> list[dict]:
    issues = []
    tables_pks = [
        ("customers",         "customer_id"),
        ("consents",          "consent_id"),
        ("consent_events",    "event_id"),
        ("sessions",          "session_id"),
        ("carts",             "cart_id"),
        ("cart_lines",        "cart_line_id"),
        ("cart_status_events","event_id"),
        ("campaigns",         "campaign_id"),
        ("message_templates", "template_id"),
        ("send_attempts",     "send_id"),
        ("orders",            "order_id"),
        ("attributions",      "attribution_id"),
        ("edge_cases",        "edge_case_id"),
    ]
    for table, pk in tables_pks:
        try:
            dupes = conn.execute(text(
                f"SELECT {pk}, COUNT(*) AS cnt FROM {table} "   # noqa: S608
                f"GROUP BY {pk} HAVING COUNT(*) > 1 LIMIT 5"
            )).fetchall()
            if dupes:
                issues.append({
                    "check":   "PK_UNIQUENESS",
                    "table":   table,
                    "count":   len(dupes),
                    "sample":  [row[0] for row in dupes],
                })
        except Exception as e:
            issues.append({"check": "PK_UNIQUENESS", "table": table, "error": str(e)})
    return issues


def _check_fk_integrity(conn) -> list[dict]:
    issues = []
    fk_checks = [
        # (child_table, child_col, parent_table, parent_col)
        ("consents",          "customer_id", "customers",   "customer_id"),
        ("consent_events",    "customer_id", "customers",   "customer_id"),
        ("sessions",          "customer_id", "customers",   "customer_id"),
        ("carts",             "customer_id", "customers",   "customer_id"),
        ("carts",             "session_id",  "sessions",    "session_id"),
        ("cart_lines",        "cart_id",     "carts",       "cart_id"),
        ("cart_status_events","cart_id",     "carts",       "cart_id"),
        ("message_templates", "campaign_id", "campaigns",   "campaign_id"),
        ("send_attempts",     "campaign_id", "campaigns",   "campaign_id"),
        ("send_attempts",     "template_id", "message_templates", "template_id"),
        ("send_attempts",     "customer_id", "customers",   "customer_id"),
        ("send_attempts",     "cart_id",     "carts",       "cart_id"),
        ("orders",            "customer_id", "customers",   "customer_id"),
        ("orders",            "cart_id",     "carts",       "cart_id"),
        ("attributions",      "order_id",    "orders",      "order_id"),
        ("attributions",      "campaign_id", "campaigns",   "campaign_id"),
        ("attributions",      "send_id",     "send_attempts","send_id"),
    ]
    for child_t, child_col, parent_t, parent_col in fk_checks:
        try:
            orphans = conn.execute(text(
                f"SELECT c.{child_col} FROM {child_t} c "   # noqa: S608
                f"LEFT JOIN {parent_t} p ON c.{child_col} = p.{parent_col} "
                f"WHERE p.{parent_col} IS NULL LIMIT 5"
            )).fetchall()
            if orphans:
                issues.append({
                    "check":      "FK_INTEGRITY",
                    "child":      f"{child_t}.{child_col}",
                    "parent":     f"{parent_t}.{parent_col}",
                    "count":      len(orphans),
                    "sample":     [row[0] for row in orphans],
                })
        except Exception as e:
            issues.append({"check": "FK_INTEGRITY", "child": f"{child_t}.{child_col}", "error": str(e)})
    return issues


def _check_enum_values(conn) -> list[dict]:
    issues = []
    enum_checks = [
        ("consents",       "channel",  ("EMAIL", "SMS")),
        ("consent_events", "channel",  ("EMAIL", "SMS")),
        ("consent_events", "event_type", ("OPT_IN", "OPT_OUT")),
        ("sessions",       "device_type", ("DESKTOP", "MOBILE", "TABLET")),
        ("carts",          "status",   ("ACTIVE", "ABANDONED", "RECOVERED", "PURCHASED")),
        ("campaigns",      "channel",  ("EMAIL", "SMS")),
        ("send_attempts",  "channel",  ("EMAIL", "SMS")),
        ("send_attempts",  "status",   ("DELIVERED", "SUPPRESSED", "FREQ_CAP", "BOUNCED")),
        ("orders",         "source",   ("DIRECT", "RECOVERY", "LATE_PURCHASE")),
        ("attributions",   "attribution_type", ("LAST_CLICK", "FIRST_CLICK", "LINEAR")),
    ]
    for table, col, allowed in enum_checks:
        vals_sql = ", ".join(f"'{v}'" for v in allowed)
        try:
            bad = conn.execute(text(
                f"SELECT DISTINCT {col} FROM {table} "   # noqa: S608
                f"WHERE {col} NOT IN ({vals_sql}) LIMIT 5"
            )).fetchall()
            if bad:
                issues.append({
                    "check":   "ENUM_VALUE",
                    "table":   table,
                    "column":  col,
                    "allowed": list(allowed),
                    "found":   [row[0] for row in bad],
                })
        except Exception as e:
            issues.append({"check": "ENUM_VALUE", "table": table, "column": col, "error": str(e)})
    return issues


def _check_date_ordering(conn) -> list[dict]:
    issues = []
    date_checks = [
        # (table, earlier_col, later_col, description)
        ("sessions",      "started_at",   "last_activity_at",  "session start <= last activity"),
        ("carts",         "created_at",   "last_activity_at",  "cart created <= last activity"),
        ("send_attempts", "scheduled_at", "sent_at",           "scheduled <= sent"),
        ("send_attempts", "sent_at",      "opened_at",         "sent <= opened"),
        ("send_attempts", "opened_at",    "clicked_at",        "opened <= clicked"),
    ]
    for table, early, late, desc in date_checks:
        try:
            bad = conn.execute(text(
                f"SELECT COUNT(*) FROM {table} "   # noqa: S608
                f"WHERE {late} IS NOT NULL AND {early} IS NOT NULL "
                f"AND {late} < {early}"
            )).scalar_one()
            if bad:
                issues.append({
                    "check":       "DATE_ORDERING",
                    "table":       table,
                    "constraint":  desc,
                    "violations":  bad,
                })
        except Exception as e:
            issues.append({"check": "DATE_ORDERING", "table": table, "error": str(e)})
    return issues


def _check_arithmetic(conn) -> list[dict]:
    issues = []
    try:
        # Cart totals must match line extensions — ignore edge-case rows
        bad = conn.execute(text("""
            SELECT c.cart_id,
                   CAST(c.total_amount AS FLOAT) AS stored,
                   CAST(SUM(cl.quantity * cl.unit_price) AS FLOAT) AS computed
            FROM carts c
            JOIN cart_lines cl ON cl.cart_id = c.cart_id
            WHERE c.issue_type IS NULL
            GROUP BY c.cart_id, c.total_amount
            HAVING ABS(CAST(c.total_amount AS FLOAT)
                       - SUM(cl.quantity * cl.unit_price)) > 0.015
            LIMIT 20
        """)).fetchall()
        if bad:
            issues.append({
                "check":  "ARITHMETIC_RECONCILIATION",
                "table":  "carts",
                "detail": "total_amount != sum(quantity*unit_price)",
                "count":  len(bad),
                "sample": [{"cart_id": r[0], "stored": r[1], "computed": r[2]} for r in bad],
            })
    except Exception as e:
        issues.append({"check": "ARITHMETIC_RECONCILIATION", "error": str(e)})

    try:
        # Attribution revenue must equal order total — ignore double-count edge cases
        bad2 = conn.execute(text("""
            SELECT a.attribution_id,
                   CAST(a.attributed_revenue AS FLOAT) AS attr_rev,
                   CAST(o.total_amount AS FLOAT) AS order_total
            FROM attributions a
            JOIN orders o ON o.order_id = a.order_id
            WHERE a.issue_type IS NULL
              AND ABS(CAST(a.attributed_revenue AS FLOAT)
                      - CAST(o.total_amount AS FLOAT)) > 0.015
            LIMIT 20
        """)).fetchall()
        if bad2:
            issues.append({
                "check":  "ATTRIBUTION_REVENUE_MATCH",
                "table":  "attributions",
                "detail": "attributed_revenue != order.total_amount",
                "count":  len(bad2),
            })
    except Exception as e:
        issues.append({"check": "ATTRIBUTION_REVENUE_MATCH", "error": str(e)})

    return issues


def _check_lifecycle_transitions(conn) -> list[dict]:
    issues = []
    try:
        # Abandoned carts must have last_activity_at at least 60 min before NOW
        # (we can't know NOW from SQL easily, so we check the status event log instead)
        # Check: every ABANDONED cart should have a cart_status_events row with to_status=ABANDONED
        bad = conn.execute(text("""
            SELECT c.cart_id FROM carts c
            LEFT JOIN cart_status_events cse
                ON cse.cart_id = c.cart_id AND cse.to_status = 'ABANDONED'
            WHERE c.status = 'ABANDONED'
              AND cse.event_id IS NULL
            LIMIT 10
        """)).fetchall()
        if bad:
            issues.append({
                "check": "LIFECYCLE_TRANSITION",
                "detail": "ABANDONED cart missing cart_status_event row",
                "count": len(bad),
                "sample": [r[0] for r in bad],
            })
    except Exception as e:
        issues.append({"check": "LIFECYCLE_TRANSITION", "error": str(e)})

    try:
        # RECOVERED carts must have an associated order
        bad2 = conn.execute(text("""
            SELECT c.cart_id FROM carts c
            LEFT JOIN orders o ON o.cart_id = c.cart_id
            WHERE c.status = 'RECOVERED'
              AND o.order_id IS NULL
            LIMIT 10
        """)).fetchall()
        if bad2:
            issues.append({
                "check": "LIFECYCLE_TRANSITION",
                "detail": "RECOVERED cart has no matching order",
                "count": len(bad2),
                "sample": [r[0] for r in bad2],
            })
    except Exception as e:
        issues.append({"check": "LIFECYCLE_TRANSITION_RECOVERED", "error": str(e)})

    return issues


def _check_duplicate_attributions(conn) -> list[dict]:
    issues = []
    try:
        # An order should have at most 1 attribution unless labelled DOUBLE_COUNT
        dupes = conn.execute(text("""
            SELECT order_id, COUNT(*) AS cnt
            FROM attributions
            WHERE issue_type IS NULL OR issue_type != 'ATTRIBUTION_DOUBLE_COUNT'
            GROUP BY order_id
            HAVING COUNT(*) > 1
            LIMIT 10
        """)).fetchall()
        if dupes:
            issues.append({
                "check":  "DUPLICATE_ATTRIBUTION",
                "detail": "Order attributed > 1 time (excluding labelled double-count edge cases)",
                "count":  len(dupes),
                "sample": [{"order_id": r[0], "cnt": r[1]} for r in dupes],
            })
    except Exception as e:
        issues.append({"check": "DUPLICATE_ATTRIBUTION", "error": str(e)})
    return issues


def _check_consent_enforcement(conn) -> list[dict]:
    issues = []
    try:
        # DELIVERED sends must have is_consented=TRUE in consents table
        # Exclude sends labelled MSG_AFTER_OPT_OUT (those are intentional edge cases)
        bad = conn.execute(text("""
            SELECT s.send_id, s.customer_id, s.channel
            FROM send_attempts s
            JOIN consents c
                ON c.customer_id = s.customer_id AND c.channel = s.channel
            WHERE s.status = 'DELIVERED'
              AND c.is_consented = FALSE
              AND (s.issue_type IS NULL OR s.issue_type != 'MSG_AFTER_OPT_OUT')
            LIMIT 10
        """)).fetchall()
        if bad:
            issues.append({
                "check":  "CONSENT_ENFORCEMENT",
                "detail": "DELIVERED send with no valid consent (non-edge-case rows)",
                "count":  len(bad),
                "sample": [{"send_id": r[0], "customer_id": r[1], "channel": r[2]} for r in bad],
            })
    except Exception as e:
        issues.append({"check": "CONSENT_ENFORCEMENT", "error": str(e)})
    return issues


# ---------------------------------------------------------------------------
# Row counts helper
# ---------------------------------------------------------------------------

def _row_counts(conn) -> dict:
    tables = [
        "customers", "consents", "consent_events", "sessions",
        "carts", "cart_lines", "cart_status_events",
        "campaigns", "message_templates", "frequency_cap_logs",
        "send_attempts", "orders", "attributions", "edge_cases",
    ]
    counts = {}
    for t in tables:
        try:
            counts[t] = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()   # noqa: S608
        except Exception:
            counts[t] = -1
    return counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate() -> dict:
    """
    Run all 8 validation checks. Returns:
      {
        "status": "PASS" | "ISSUES_FOUND",
        "counts": {...},
        "issues": [...],
        "summary": {"total_issues": N, "checks_passed": M, "checks_failed": K}
      }
    """
    all_issues: list[dict] = []

    with engine.connect() as conn:
        all_issues += _check_pk_uniqueness(conn)
        all_issues += _check_fk_integrity(conn)
        all_issues += _check_enum_values(conn)
        all_issues += _check_date_ordering(conn)
        all_issues += _check_arithmetic(conn)
        all_issues += _check_lifecycle_transitions(conn)
        all_issues += _check_duplicate_attributions(conn)
        all_issues += _check_consent_enforcement(conn)
        counts = _row_counts(conn)

    check_names = {i["check"] for i in all_issues}
    all_checks  = {
        "PK_UNIQUENESS", "FK_INTEGRITY", "ENUM_VALUE", "DATE_ORDERING",
        "ARITHMETIC_RECONCILIATION", "ATTRIBUTION_REVENUE_MATCH",
        "LIFECYCLE_TRANSITION", "LIFECYCLE_TRANSITION_RECOVERED",
        "DUPLICATE_ATTRIBUTION", "CONSENT_ENFORCEMENT",
    }
    passed = all_checks - check_names

    result = {
        "status":  "PASS" if not all_issues else "ISSUES_FOUND",
        "counts":  counts,
        "issues":  all_issues,
        "summary": {
            "total_issues":  len(all_issues),
            "checks_failed": len(check_names),
            "checks_passed": len(passed),
        },
    }
    return result


if __name__ == "__main__":
    import json
    report = validate()
    print(json.dumps(report, indent=2, default=str))
