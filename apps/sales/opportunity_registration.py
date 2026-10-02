"""Server-owned VF registration using the existing Sales visibility rules."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.rbac.data_visibility_mixin import build_visibility_filter

from .models import Deal, OpportunityNumberSequence


def _owner_filter_for_users(predicate):
    """Translate only established owner predicates; unknown scopes fail closed."""
    translated = Q()
    translated.connector = predicate.connector
    translated.negated = predicate.negated
    for child in predicate.children:
        if isinstance(child, Q):
            translated.children.append(_owner_filter_for_users(child))
            continue
        lookup, value = child
        if lookup == 'owner':
            translated.children.append(('pk', getattr(value, 'pk', value)))
        elif lookup in {'owner__id__in', 'owner__pk__in'}:
            translated.children.append(('pk__in', value))
        else:
            raise ValueError('Unsupported Sales owner visibility predicate.')
    return translated


def visible_opportunity_owners(actor):
    """Active assignees within the same owner scope as the Sales Deal list."""
    users = get_user_model().objects.filter(is_active=True)
    if not actor or not actor.is_authenticated or not actor.is_active:
        return users.none()
    predicate = build_visibility_filter(
        user=actor, module_code='sales', owner_field='owner', model_class=Deal,
    )
    try:
        predicate = _owner_filter_for_users(predicate)
    except ValueError:
        return users.none()
    return users.filter(predicate).distinct().order_by('first_name', 'last_name', 'pk')


@transaction.atomic
def create_registered_opportunity(*, actor, validated_data):
    """Allocate and persist together; a failed registration consumes no number."""
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise ValidationError({'created_by': 'An active authenticated creator is required.'})

    values = dict(validated_data)
    owner = values.get('owner') or actor
    if not visible_opportunity_owners(actor).filter(pk=owner.pk).exists():
        raise ValidationError({'owner': 'Choose an active owner within your Sales access.'})
    values['owner'] = owner
    values['created_by'] = actor
    # Email conversion deliberately supplies None when no source date exists.
    values.setdefault('open_date', timezone.localdate())
    team_members = values.pop('team_members', [])

    # The migration seeds this singleton; get_or_create also supports a newly
    # initialized model-sync database. The unique primary key serializes setup.
    OpportunityNumberSequence.objects.get_or_create(pk=1)
    sequence = OpportunityNumberSequence.objects.select_for_update().get(pk=1)
    number = sequence.next_number
    while Deal.objects.filter(deal_code=f'Q-{number}').exists():
        number += 1
    values['deal_code'] = f'Q-{number}'
    sequence.next_number = number + 1
    sequence.save(update_fields=['next_number'])
    deal = Deal.objects.create(**values)
    if team_members:
        deal.team_members.set(team_members)
    from .opportunity_workspace import register_workspace
    register_workspace(deal, actor)
    return deal
