"""
Auto-create + activate the Electrical legend in apps.pid_checker_v2's
shared PidCheckerV2LegendSheet — PER USER, on first use of Electrical
Comparison. NOT a global server-startup seed.

Why not the apps.instrument_io_workflow.services.seed_default_legends
pattern this was asked to follow: that works because IOListLegendSheet
supports a single ownerless, shared default row (created_by=None,
is_default=True). PidCheckerV2LegendSheet has no such concept —
created_by is a REQUIRED ForeignKey (no null=True), and its own active-
legend uniqueness constraint (uniq_pidv2_active_legend_per_user_section_
project) is scoped per (user, section, project), never global. A single
process-startup seed with no logged-in user literally cannot satisfy
that NOT NULL constraint, and even picking one arbitrary owner would
only make the legend active for THAT one user — everyone else would
still see nothing, which defeats "no manual setup needed."

This does the same THING (no manual legend setup needed) a different
way: the same per-user auto-create-on-open pattern ValveMTO.jsx already
uses client-side (autoPopulateLegendSection) — here, server-side, called
once per user the first time they hit the Electrical Comparison upload
endpoint (see apps.electrical_comparison.views.UploadComparisonView).

Idempotent: skipped entirely if this user already has ANY active legend
for section='electrical' — never duplicates, never clobbers a legend the
user has since hand-edited via the Legend Sheets Canvas UI.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ELECTRICAL_SECTION = 'electrical'


def seed_electrical_legend_for_user(user) -> dict:
    """Ensure `user` has an active Electrical legend in
    PidCheckerV2LegendSheet, auto-creating one from
    DEFAULT_TEMPLATES[SECTION_ELECTRICAL] (apps.pid_checker_v2.
    legend_defaults — all 8 type codes: PM, NPM, U, JB, BD, AB, SB, TSG)
    if they don't already have one.

    Returns {'created': bool, 'legend_id': str | None}. Never raises for
    a missing/anonymous user — returns a no-op result instead, since this
    is a best-effort convenience, not a hard requirement to use
    Electrical Comparison (same "never let an enrichment step break the
    core feature" principle used elsewhere in this codebase).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return {'created': False, 'legend_id': None}

    try:
        from apps.pid_checker_v2.models import PidCheckerV2LegendSheet
        from apps.pid_checker_v2.legend_defaults import DEFAULT_TEMPLATES, SECTION_ELECTRICAL

        existing = PidCheckerV2LegendSheet.objects.filter(
            created_by=user, section=ELECTRICAL_SECTION, is_active=True,
        ).first()
        if existing is not None:
            return {'created': False, 'legend_id': str(existing.legend_id)}

        template = DEFAULT_TEMPLATES[SECTION_ELECTRICAL]
        legend = PidCheckerV2LegendSheet.objects.create(
            created_by=user,
            section=ELECTRICAL_SECTION,
            name=template['name'],
            description=template.get('description', ''),
            definition=template.get('definition', {}),
            is_active=True,
        )
        logger.info(
            '[ElecCompare] Auto-seeded Electrical legend for user_id=%s legend_id=%s',
            user.id, legend.legend_id,
        )
        return {'created': True, 'legend_id': str(legend.legend_id)}
    except Exception as exc:  # noqa: BLE001
        logger.warning('[ElecCompare] Electrical legend auto-seed failed for user_id=%s: %s', getattr(user, 'id', None), exc)
        return {'created': False, 'legend_id': None}
