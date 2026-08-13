from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.onboarding.views import OnboardingRecordViewSet


class OnboardingRecordUserFilterTests(SimpleTestCase):
    def test_queryset_is_scoped_to_selected_user(self):
        queryset = MagicMock()
        scoped_queryset = MagicMock()
        queryset.all.return_value = queryset
        queryset.filter.return_value = scoped_queryset
        scoped_queryset.annotate.return_value = scoped_queryset
        scoped_queryset.select_related.return_value = scoped_queryset

        view = OnboardingRecordViewSet()
        view.action = 'list'
        view.request = SimpleNamespace(query_params={'user_id': '42'})
        view.queryset = queryset

        result = view.get_queryset()

        queryset.filter.assert_called_once_with(user_id=42)
        self.assertIs(result, scoped_queryset)

    def test_invalid_user_id_is_rejected(self):
        view = OnboardingRecordViewSet()
        view.action = 'list'
        view.request = SimpleNamespace(query_params={'user_id': 'invalid'})
        view.queryset = MagicMock()
        view.queryset.all.return_value = view.queryset

        with self.assertRaises(ValidationError):
            view.get_queryset()
