"""One authorized, assured and atomic transition into schedule approval."""
from django.db import transaction
from django.utils import timezone

from ..access import can_final_approve_defaults
from apps.rbac.approval_eligibility import active_approval_user, approval_access
from ..models import ScheduleReview, ScheduleVersion
from .audit import record_event
from .trustworthy_scheduling import current_assurance


class ScheduleApprovalError(ValueError):
    def __init__(self, message, *, code, status_code=409, **details):
        super().__init__(message)
        self.status_code = status_code
        self.payload = {'error': message, 'code': code, **details}


def lock_schedule_version(version):
    # Do not join nullable relations into FOR UPDATE (PostgreSQL rejects that).
    return ScheduleVersion.objects.select_for_update().get(
        pk=version.pk, is_deleted=False,
    )


def require_schedule_authority(version, user):
    if not getattr(user, 'is_authenticated', False) or not can_final_approve_defaults(user, version.schedule.project):
        raise ScheduleApprovalError(
            'Only a project authority can approve a schedule version.',
            code='schedule_approval_forbidden', status_code=403,
        )


def current_schedule_version(version):
    return bool(not version.is_deleted and not version.schedule.is_deleted
                and not version.schedule.project.is_deleted and version.status != 'superseded'
                and not version.schedule.versions.filter(is_deleted=False, version__gt=version.version).exists())


def can_decide_schedule_review(review, user, *, decision='approved'):
    if not active_approval_user(user) or not approval_access(user, 'planning_package'):
        return False
    version = review.version
    if (review.is_deleted or review.status != 'pending' or not current_schedule_version(version)
            or version.status != 'calculated' or not version.calculated_at
            or (decision == 'approved' and version.calculated_at > review.requested_at)):
        return False
    votes = list(review.decisions.filter(is_deleted=False).select_related('reviewer'))
    vote = next((item for item in votes if item.reviewer_id == user.pk), None)
    if not vote or vote.status != 'pending':
        return False
    if can_final_approve_defaults(user, version.schedule.project):
        return not any(item.pk != vote.pk and item.status != 'approved'
                       and not can_final_approve_defaults(item.reviewer, version.schedule.project)
                       for item in votes)
    return True


def can_approve_schedule(version, user, *, allow_unapproved_assurance=False):
    if (not can_final_approve_defaults(user, version.schedule.project)
            or not current_schedule_version(version) or version.status != 'calculated'
            or not version.calculated_at
            or version.governance_reviews.filter(is_deleted=False, status='pending').exists()):
        return False
    assurance = current_assurance(version)
    return bool(assurance and not assurance.is_deleted and not assurance.blockers
                and assurance.status in ({'ready', 'approved'} if allow_unapproved_assurance else {'approved'}))


def can_baseline_schedule(version, user):
    if (not can_final_approve_defaults(user, version.schedule.project)
            or not current_schedule_version(version) or version.status != 'approved'
            or version.governance_reviews.filter(is_deleted=False, status='pending').exists()
            or version.governance_items.filter(priority='critical', is_deleted=False)
            .exclude(status__in=['closed', 'implemented', 'rejected']).exists()):
        return False
    assurance = current_assurance(version)
    return bool(assurance and not assurance.is_deleted and assurance.status == 'approved' and not assurance.blockers)


@transaction.atomic
def approve_schedule_version(version, user, *, route='direct', review_id=None):
    version = lock_schedule_version(version)
    require_schedule_authority(version, user)
    if version.schedule.is_deleted or version.schedule.project.is_deleted:
        raise ScheduleApprovalError('This schedule is archived.', code='schedule_archived')
    if version.status != 'calculated' or not version.calculated_at:
        raise ScheduleApprovalError(
            'Only a calculated version can be approved.', code='schedule_approval_state',
        )
    if not current_schedule_version(version):
        raise ScheduleApprovalError('This schedule version is no longer current.', code='schedule_version_stale')
    assurance = current_assurance(version)
    if not assurance or assurance.is_deleted or assurance.status != 'approved':
        raise ScheduleApprovalError(
            'Run and approve Phase 3 schedule assurance before schedule approval.',
            code='schedule_assurance_required',
        )
    generation = version.source_generation
    critical_findings = [
        item for item in (generation.validation or []) if item.get('severity') == 'critical'
    ] if generation else []
    unconfirmed_gates = [
        item for item in (generation.logic_matrix or [])
        if item.get('source') == 'dependency_template' and item.get('requires_confirmation')
    ] if generation else []
    if assurance.blockers or critical_findings or unconfirmed_gates:
        raise ScheduleApprovalError(
            'Resolve critical generation findings and confirm engineering release gates before approval.',
            code='schedule_assurance_blocked', critical_finding_count=len(critical_findings),
            unconfirmed_gate_count=len(unconfirmed_gates), assurance_blocker_count=len(assurance.blockers or []),
        )
    pending_reviews = version.governance_reviews.filter(is_deleted=False, status='pending')
    if route == 'governance_review':
        review = pending_reviews.filter(pk=review_id).first()
        if (review is None or not review.decisions.filter(is_deleted=False).exists()
                or review.decisions.filter(is_deleted=False).exclude(status='approved').exists()
                or pending_reviews.exclude(pk=review_id).exists()):
            raise ScheduleApprovalError('All assigned reviewers must approve the current review.', code='schedule_review_incomplete')
    elif pending_reviews.exists():
        raise ScheduleApprovalError('Complete the assigned schedule review first.', code='schedule_review_pending')
    version.status = 'approved'
    version.save(update_fields=['status', 'updated_at'])
    record_event(
        project=version.schedule.project, actor=user, action='schedule.approved', entity=version,
        before={'status': 'calculated'},
        after={
            'status': 'approved', 'assurance_review_id': assurance.pk,
            'calculated_at': version.calculated_at.isoformat(),
            'assurance_input_fingerprint': assurance.input_fingerprint,
        },
        metadata={'approval_route': route, 'review_id': review_id},
    )
    return version


@transaction.atomic
def decide_schedule_review(version, review_id, user, *, decision, comment=''):
    version = lock_schedule_version(version)
    review = ScheduleReview.objects.select_for_update().filter(
        pk=review_id, version=version, is_deleted=False,
    ).first()
    if review is None:
        raise ScheduleApprovalError('Review not found.', code='schedule_review_missing', status_code=404)
    if review.status != 'pending':
        raise ScheduleApprovalError('This review is already complete.', code='schedule_review_complete')
    votes = list(review.decisions.select_for_update().filter(is_deleted=False).select_related('reviewer'))
    vote = next((item for item in votes if item.reviewer_id == user.pk), None)
    if vote is None:
        raise ScheduleApprovalError(
            'You are not assigned to this review.', code='schedule_review_unassigned', status_code=403,
        )
    if not approval_access(user, 'planning_package') or not active_approval_user(user):
        raise ScheduleApprovalError('Your current access does not permit this review.', code='schedule_review_forbidden', status_code=403)
    if vote.status != 'pending':
        raise ScheduleApprovalError('Your review decision is already recorded.', code='schedule_review_complete')
    if decision not in {'approved', 'rejected', 'changes_requested'}:
        raise ScheduleApprovalError('Unsupported review decision.', code='schedule_review_decision', status_code=400)
    if (not current_schedule_version(version) or version.status != 'calculated'
            or not version.calculated_at or (decision == 'approved' and version.calculated_at > review.requested_at)):
        raise ScheduleApprovalError('This review no longer matches the current schedule.', code='schedule_review_stale')
    if can_final_approve_defaults(user, version.schedule.project) and any(
        item.pk != vote.pk and item.status != 'approved'
        and not can_final_approve_defaults(item.reviewer, version.schedule.project) for item in votes
    ):
        raise ScheduleApprovalError('Remaining reviewers must respond before final authority approval.', code='schedule_review_awaiting_reviewers')
    previous_status = vote.status
    vote.status, vote.comment, vote.decided_at = decision, comment, timezone.now()
    vote.save(update_fields=['status', 'comment', 'decided_at', 'updated_at'])
    statuses = [item.status for item in votes]
    if 'rejected' in statuses:
        review.status = 'rejected'
    elif 'changes_requested' in statuses:
        review.status = 'changes_requested'
    elif statuses and all(value == 'approved' for value in statuses):
        approve_schedule_version(version, user, route='governance_review', review_id=review.pk)
        review.status = 'approved'
    if review.status != 'pending':
        review.completed_at = timezone.now()
    review.save(update_fields=['status', 'completed_at', 'updated_at'])
    record_event(
        project=version.schedule.project, actor=user, action='governance.review_decided', entity=review,
        before={'decision': previous_status, 'review_status': 'pending'},
        after={
            'version_id': version.pk, 'decision_id': vote.pk, 'reviewer_id': user.pk,
            'decision': vote.status, 'review_status': review.status,
        },
        metadata={
            'closed_after_recalculation': bool(version.calculated_at > review.requested_at),
            'review_requested_at': review.requested_at.isoformat(),
            'calculated_at': version.calculated_at.isoformat(),
        },
    )
    return review
