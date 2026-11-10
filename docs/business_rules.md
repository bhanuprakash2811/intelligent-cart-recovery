# Business rules (MVP assumptions)

1. A cart is considered abandoned in the synthetic baseline when its generated inactivity exceeds 60 minutes and it has no completed order.
2. Consent is channel-specific (EMAIL/SMS). A message is DELIVERED only when the generated consent state is true; otherwise it is SUPPRESSED.
3. Email and SMS campaigns are selected by the channel chosen for the send attempt.
4. A clicked message may produce a recovery order within a 72-hour attribution window.
5. One order may have at most one attribution (`UNIQUE(order_id)`).
6. All thresholds and volumes are assumptions for the MVP and must be aligned to the exact challenge statement if it specifies different values.
