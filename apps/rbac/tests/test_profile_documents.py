from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from apps.rbac.views import ProfileDocumentViewSet


class ProfileDocumentEmployeeScopeTests(SimpleTestCase):
    @patch('apps.rbac.approval_eligibility.can_review_profile_document', return_value=False)
    @patch('apps.rbac.views.CanManageUsers.has_permission', return_value=True)
    def test_rbac_user_manager_alone_cannot_review_employee_documents(self, can_manage, eligibility):
        view = ProfileDocumentViewSet()
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_staff=False),
        )

        self.assertFalse(view._can_review_documents(request))
        eligibility.assert_called_once_with(request.user)
        can_manage.assert_not_called()

    @patch('apps.rbac.approval_eligibility.can_review_profile_document', return_value=False)
    @patch('apps.rbac.models.ProfileDocument.objects')
    def test_staff_list_is_scoped_to_selected_employee(self, documents, eligibility):
        queryset = MagicMock()
        filtered_queryset = MagicMock()
        documents.select_related.return_value = queryset
        queryset.all.return_value = queryset
        queryset.filter.return_value = filtered_queryset

        view = ProfileDocumentViewSet()
        view.request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_staff=True),
            query_params={'user_id': '42'},
        )

        result = view.get_queryset()

        self.assertIs(result, filtered_queryset)
        queryset.filter.assert_called_once_with(user_profile__user_id=42)

    @patch('apps.rbac.views.UserProfile.objects.get')
    @patch('apps.rbac.models.ProfileDocument.objects')
    def test_staff_upload_assigns_document_to_selected_employee(self, documents, get_profile):
        target_profile = MagicMock()
        get_profile.return_value = target_profile
        serializer = MagicMock(validated_data={'document_type': 'passport'})

        view = ProfileDocumentViewSet()
        view.request = SimpleNamespace(
            user=SimpleNamespace(pk=1, email='admin@example.com', is_superuser=False, is_staff=True),
            data={'target_user_id': '42'},
            FILES={'document_file': MagicMock()},
        )

        view.perform_create(serializer)

        get_profile.assert_called_once_with(user_id='42')
        documents.filter.assert_called_once_with(
            user_profile=target_profile,
            document_type='passport',
            is_active=True,
        )
        documents.filter.return_value.update.assert_called_once_with(is_active=False)
        serializer.save.assert_called_once_with(user_profile=target_profile, is_active=True)

    def test_regular_user_cannot_upload_for_another_employee(self):
        serializer = MagicMock(validated_data={'document_type': 'passport'})
        view = ProfileDocumentViewSet()
        view.request = SimpleNamespace(
            user=SimpleNamespace(pk=1, is_superuser=False, is_staff=False),
            data={'target_user_id': '42'},
        )

        with self.assertRaises(PermissionDenied):
            view.perform_create(serializer)

        serializer.save.assert_not_called()
