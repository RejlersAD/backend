"""Admin exposure and mutation guards for synthetic captured email evidence."""

from importlib import import_module
from decimal import Decimal

from django.conf import settings
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission as DjangoPermission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase, override_settings
from django.urls import clear_url_caches, path, reverse
from django.utils import timezone

from apps.rbac.models import Permission, Role, UserPermissionOverride, UserRole
from apps.sales.models import Client, Deal, SalesEmailIntake, SalesMailboxConnection

from .access_fixtures import grant_sales_actions


urlpatterns = []


@override_settings(
    INSTALLED_APPS=[
        *settings.INSTALLED_APPS,
        'django.contrib.admin.apps.SimpleAdminConfig',
        'django.contrib.messages',
    ],
    MIDDLEWARE=[*settings.MIDDLEWARE, 'django.contrib.messages.middleware.MessageMiddleware'],
    ROOT_URLCONF=__name__,
)
class SalesMailboxCaptureAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        users = get_user_model()
        self.owner = users.objects.create_user('capture-admin-owner', email='owner@example.test', is_staff=True)
        self.other = users.objects.create_user('capture-admin-other', email='other@example.test', is_staff=True)
        self.admin_user = users.objects.create_user('capture-admin-role', email='admin@example.test', is_staff=True)
        self.staff_only = users.objects.create_user('capture-admin-staff', email='staff@example.test', is_staff=True)
        content_type = ContentType.objects.get_for_model(SalesEmailIntake)
        django_permissions = [DjangoPermission.objects.get_or_create(
            content_type=content_type, codename=f'{action}_salesemailintake',
            defaults={'name': f'Can {action} synthetic email intake'},
        )[0] for action in ('view', 'change', 'delete', 'add')]
        for user in (self.owner, self.other, self.admin_user, self.staff_only):
            user.user_permissions.add(*django_permissions)
        for user in (self.owner, self.other, self.admin_user):
            grant_sales_actions(user, 'sales_email_intake')
        role, _ = Role.objects.get_or_create(code='ict_admin', defaults={'name': 'Synthetic ICT admin', 'level': 2})
        UserRole.objects.get_or_create(user_profile=self.admin_user.rbac_profile, role=role)

        self.connection = self.mailbox('owned', self.owner)
        self.other_connection = self.mailbox('other', self.other)
        self.system_connection = self.mailbox('system', None)
        self.captured = self.intake('owned-first', self.connection)
        self.same_mailbox = self.intake('owned-second', self.connection)
        self.hidden = self.intake('hidden-other', self.other_connection)
        self.system = self.intake('hidden-system', self.system_connection)
        self.legacy = self.intake('legacy-first')
        self.legacy_second = self.intake('legacy-second')

        admin_module = import_module('apps.sales.admin')
        self.site = AdminSite(name='capture_admin')
        self.site.register(SalesEmailIntake, admin_module.SalesEmailIntakeAdmin)
        self.model_admin = self.site._registry[SalesEmailIntake]
        global urlpatterns
        urlpatterns = [path('capture-admin/', self.site.urls)]
        clear_url_caches()
        self.addCleanup(clear_url_caches)
        self.factory = RequestFactory()
        self.client.force_login(self.owner)

    def mailbox(self, name, owner):
        return SalesMailboxConnection.objects.create(
            name=f'Synthetic {name} mailbox', auth_mode='application',
            tenant_id='synthetic-tenant', client_id='synthetic-client',
            mailbox_address=f'{name}@example.test', created_by=owner,
        )

    def intake(self, name, connection=None):
        return SalesEmailIntake.objects.create(
            source_message_id=f'synthetic-{name}', subject=f'Synthetic {name} enquiry',
            sender_email='client@example.test', received_at=timezone.now(),
            body_preview=f'Synthetic {name} confidential source', mailbox_connection=connection,
            source_mailbox_address=connection.mailbox_address if connection else '',
            source_tenant_id=connection.tenant_id if connection else '',
            conversation_id=f'synthetic-conversation-{name}' if connection else '',
            captured_by=self.owner if connection else None,
        )

    def request(self, user=None):
        request = self.factory.get('/capture-admin/')
        request.user = user or self.owner
        return request

    def url(self, action='changelist', obj=None):
        return reverse(f'capture_admin:sales_salesemailintake_{action}', args=[obj.pk] if obj else None)

    def test_owner_list_search_and_counts_never_include_other_or_system_mail(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        changelist = response.context_data['cl']
        self.assertEqual(changelist.result_count, 4)
        self.assertIsNone(changelist.full_result_count)
        self.assertSetEqual(set(changelist.result_list.values_list('pk', flat=True)), {
            self.captured.pk, self.same_mailbox.pk, self.legacy.pk, self.legacy_second.pk,
        })
        self.assertNotContains(response, self.hidden.subject)
        self.assertNotContains(response, self.system.subject)
        searched = self.client.get(self.url(), {'q': 'hidden-other'})
        self.assertEqual(searched.context_data['cl'].result_count, 0)
        self.assertNotContains(searched, self.hidden.subject)

    def test_staff_and_django_model_permissions_do_not_grant_sales_source_access(self):
        self.client.force_login(self.staff_only)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.get(self.url('change', self.captured)).status_code, 403)
        request = self.request(self.staff_only)
        self.assertFalse(self.model_admin.has_module_permission(request))
        self.assertFalse(self.model_admin.has_view_permission(request, self.captured))
        self.assertEqual(self.model_admin.get_queryset(request).count(), 0)

    def test_different_owner_cannot_read_or_post_another_capture(self):
        self.client.force_login(self.other)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(self.url('change', self.captured), {'status': 'rejected'})
            self.assertIn(response.status_code, (302, 403))
            self.assertNotIn(self.captured.body_preview.encode(), response.content)
        self.captured.refresh_from_db()
        self.assertEqual(self.captured.status, 'received')
        self.assertFalse(self.model_admin.has_view_permission(self.request(self.other), self.captured))

    def test_existing_ict_admin_scope_includes_system_owned_captures(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data['cl'].result_count, 6)
        self.assertContains(response, self.system.subject)

    def test_explicit_sales_read_denial_overrides_admin_role(self):
        permission = Permission.objects.filter(module__code='sales_email_intake', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.admin_user.rbac_profile, permission=permission, allowed=False)
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.model_admin.get_queryset(self.request(self.admin_user)).count(), 0)

    def test_capture_is_view_only_and_post_cannot_rewrite_or_unlink_source(self):
        before = SalesEmailIntake.objects.values().get(pk=self.captured.pk)
        request = self.request()
        self.assertTrue(self.model_admin.has_view_permission(request, self.captured))
        self.assertFalse(self.model_admin.has_change_permission(request, self.captured))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.captured))
        readonly = set(self.model_admin.get_readonly_fields(request, self.captured))
        self.assertSetEqual(readonly, {field.name for field in SalesEmailIntake._meta.concrete_fields})
        viewed = self.client.get(self.url('change', self.captured))
        self.assertEqual(viewed.status_code, 200)
        self.assertContains(viewed, self.captured.body_preview)
        attempted = self.client.post(self.url('change', self.captured), {
            'mailbox_connection': '', 'source_message_id': 'forged-id',
            'body_preview': 'forged-body', 'status': 'converted',
        })
        self.assertEqual(attempted.status_code, 403)
        self.assertEqual(before, SalesEmailIntake.objects.values().get(pk=self.captured.pk))

    def test_stale_related_capture_and_opportunity_labels_are_not_rendered(self):
        client = Client.objects.create(company_name='Hidden synthetic commercial client', industry_type='other')
        opportunity = Deal.objects.create(
            deal_name='Hidden synthetic commercial opportunity', client=client,
            estimated_value=Decimal('100.00'), expected_close_date='2026-12-01', owner=self.other,
        )
        SalesEmailIntake.objects.filter(pk=self.captured.pk).update(duplicate_of=self.hidden, opportunity=opportunity)
        response = self.client.get(self.url('change', self.captured))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.hidden.subject)
        self.assertNotContains(response, opportunity.deal_name)
        listed = self.client.get(self.url())
        self.assertNotContains(listed, opportunity.deal_name)

    def test_no_generic_add_and_no_new_mailbox_admin_surface(self):
        self.assertFalse(self.model_admin.has_add_permission(self.request(self.admin_user)))
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get(self.url('add')).status_code, 403)
        self.assertEqual(self.client.post(self.url('add'), {'subject': 'forged'}).status_code, 403)
        from django.contrib import admin
        self.assertFalse(admin.site.is_registered(SalesMailboxConnection))

    def test_duplicate_choices_and_validation_preserve_legacy_and_mailbox_scope(self):
        request = self.request()
        form_class = self.model_admin.get_form(request, self.legacy)
        self.assertSetEqual(set(form_class.base_fields['duplicate_of'].queryset.values_list('pk', flat=True)), {self.legacy_second.pk})
        form = form_class(data={'status': 'duplicate', 'duplicate_of': str(self.hidden.pk)}, instance=self.legacy)
        self.assertFalse(form.is_valid())
        self.assertIn('duplicate_of', form.errors)
        self.assertSetEqual(set(self.model_admin._duplicate_queryset(request, self.captured).values_list('pk', flat=True)), {self.same_mailbox.pk})
        SalesEmailIntake.objects.filter(pk=self.legacy.pk).update(duplicate_of=self.hidden)
        response = self.client.get(self.url('change', self.legacy))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.hidden.subject)

    def test_legacy_review_save_preserves_every_source_field(self):
        before = SalesEmailIntake.objects.values().get(pk=self.legacy.pk)
        self.legacy.status = 'under_review'
        self.legacy.source_message_id = 'forged-legacy-id'
        self.legacy.mailbox_connection = self.connection
        self.legacy.body_preview = 'forged source'
        self.legacy.sent_at = timezone.now()
        self.model_admin.save_model(self.request(), self.legacy, None, change=True)
        after = SalesEmailIntake.objects.values().get(pk=self.legacy.pk)
        self.assertEqual(after['status'], 'under_review')
        for name in self.model_admin.readonly_fields:
            if name != 'updated_at':
                key = SalesEmailIntake._meta.get_field(name).attname
                self.assertEqual(after[key], before[key])

    def test_save_rechecks_actual_capture_even_if_instance_is_forged_as_legacy(self):
        before = SalesEmailIntake.objects.values().get(pk=self.captured.pk)
        self.captured.mailbox_connection = None
        self.captured.status = 'rejected'
        with self.assertRaises(PermissionDenied):
            self.model_admin.save_model(self.request(self.admin_user), self.captured, None, change=True)
        self.assertEqual(before, SalesEmailIntake.objects.values().get(pk=self.captured.pk))

    def test_bulk_delete_is_atomic_and_cannot_delete_capture_or_hidden_record(self):
        ids = [self.legacy.pk, self.captured.pk]
        with self.assertRaises(PermissionDenied):
            self.model_admin.delete_queryset(self.request(), SalesEmailIntake.objects.filter(pk__in=ids))
        self.assertEqual(SalesEmailIntake.objects.filter(pk__in=ids).count(), 2)
        with self.assertRaises(PermissionDenied):
            self.model_admin.delete_model(self.request(), self.hidden)
        self.model_admin.delete_model(self.request(), self.legacy)
        self.assertFalse(SalesEmailIntake.objects.filter(pk=self.legacy.pk).exists())
