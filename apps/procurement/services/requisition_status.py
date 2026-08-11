"""Canonical Purchase Requisition lifecycle statuses and legacy aliases."""

CANONICAL_PR_STATUSES = (
    'draft',
    'submitted',
    'in_review',
    'approved',
    'rejected',
    'cancelled',
    'converted',
)

LEGACY_PR_STATUS_MAP = {
    'pending_level_2': 'in_review',
    'pm_approved': 'in_review',
    'vp_approved': 'approved',
    'fully_approved': 'approved',
}


def canonicalize_pr_status(value):
    """Return the public lifecycle status for a stored status value."""
    return LEGACY_PR_STATUS_MAP.get(value, value)


def stored_values_for(canonical_status):
    """Include legacy database values until the data migration is deployed."""
    return {
        canonical_status,
        *(
            legacy
            for legacy, canonical in LEGACY_PR_STATUS_MAP.items()
            if canonical == canonical_status
        ),
    }
