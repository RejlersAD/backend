"""Client form choices derived from the existing Sales visibility rules."""
from django.contrib.auth import get_user_model
from apps.rbac.data_visibility_mixin import build_visibility_filter
from .models import Client
from .opportunity_registration import _owner_filter_for_users


def visible_client_owners(actor):
    users = get_user_model().objects.filter(is_active=True)
    if not actor or not actor.is_authenticated or not actor.is_active:
        return users.none()
    predicate = build_visibility_filter(user=actor, module_code='sales',
        owner_field='owner', model_class=Client)
    try:
        predicate = _owner_filter_for_users(predicate)
    except ValueError:
        return users.none()
    return users.filter(predicate).order_by('first_name', 'last_name', 'pk')
