"""Source-neutral registration, provenance, numbering and commercial unknowns."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import RolePermission
from apps.sales.email_permissions import visible_email_clients
from apps.sales.models import Client, Deal, OpportunityNumberSequence
from apps.sales.opportunity_registration import (
    create_registered_opportunity, visible_opportunity_owners,
)
from apps.sales.serializers import (
    ClientDetailSerializer, ClientListSerializer, DealCreateSerializer,
    DealDetailSerializer, DealListSerializer,
)
from apps.sales.tests.access_fixtures import grant_sales_actions


class VFRegistrationCoreTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.actor = user.objects.create_user(
            username='vf-creator', email='vf-creator@example.test', first_name='Creator',
        )
        self.peer = user.objects.create_user(
            username='vf-owner', email='vf-owner@example.test', first_name='Owner',
        )
        self.outsider = user.objects.create_user(username='outside', email='outside@example.test')
        grant_sales_actions(self.actor, 'sales', 'sales_opportunities', 'sales_clients')
        grant_sales_actions(self.peer, 'sales', 'sales_opportunities', 'sales_clients')
        self.client = Client.objects.create(
            client_code='VF-CLIENT', company_name='VF synthetic client',
            industry_type='other', account_manager=self.actor,
        )
        OpportunityNumberSequence.objects.create(pk=1)

    def serializer(self, **overrides):
        payload = {'deal_name': 'Synthetic enquiry', 'client': str(self.client.pk), **overrides}
        return DealCreateSerializer(data=payload, context={'actor': self.actor})

    def register(self, **overrides):
        serializer = self.serializer(**overrides)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.save()

    def test_minimal_registration_issues_vf_and_preserves_unknowns(self):
        deal = self.register()
        deal.refresh_from_db()
        self.assertEqual(deal.deal_code, 'Q-102101')
        self.assertEqual(deal.created_by, self.actor)
        self.assertEqual(deal.owner, self.actor)
        self.assertEqual(deal.open_date, timezone.localdate())
        self.assertEqual(deal.stage, 'lead')
        self.assertEqual(DealListSerializer(deal).data['stage_display'], 'Open')
        self.assertEqual(deal.opportunity_type, '')
        self.assertIsNone(deal.estimated_value)
        self.assertIsNone(deal.weighted_value)
        self.assertIsNone(deal.expected_close_date)
        self.assertIsNone(deal.submission_due_date)
        self.assertEqual(deal.currency, '')

    def test_server_identity_overrides_supplied_code_creator_and_stage(self):
        deal = self.register(
            deal_code='Q-1', created_by=self.peer.pk, owner=self.peer.pk,
            opportunity_type='rfq', stage='awarded',
        )
        self.assertEqual(deal.deal_code, 'Q-102101')
        self.assertEqual(deal.created_by, self.actor)
        self.assertEqual(deal.owner, self.peer)
        self.assertEqual(deal.opportunity_type, 'rfq')
        self.assertEqual(deal.stage, 'lead')

    def test_date_inputs_remain_distinct_and_explicit_unknown_is_not_today(self):
        deal = self.register(open_date=None, submission_due_date='2026-10-02')
        self.assertIsNone(deal.open_date)
        self.assertEqual(deal.submission_due_date, date(2026, 10, 2))
        dated = self.register(open_date='2026-09-29')
        self.assertEqual(dated.open_date, date(2026, 9, 29))

    def test_zero_is_known_and_weighted_estimate_uses_stage_probability(self):
        zero = self.register(estimated_value='0.00', currency='AED')
        self.assertEqual(zero.weighted_value, Decimal('0.00'))
        estimated = self.register(estimated_value='150.00', currency='AED')
        self.assertEqual(estimated.weighted_value, Decimal('15.00'))
        estimated.estimated_value = None
        estimated.save()
        estimated.refresh_from_db()
        self.assertIsNone(estimated.weighted_value)

    def test_updates_cannot_change_code_or_creator(self):
        deal = self.register(owner=self.peer.pk)
        serializer = DealCreateSerializer(
            deal, data={'deal_code': 'Q-7', 'created_by': self.peer.pk, 'deal_name': 'Reviewed title'},
            partial=True, context={'actor': self.actor},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        deal.refresh_from_db()
        self.assertEqual(deal.deal_code, 'Q-102101')
        self.assertEqual(deal.created_by, self.actor)
        self.assertEqual(deal.deal_name, 'Reviewed title')

    def test_creator_history_does_not_claim_current_owner_created_record(self):
        deal = self.register(owner=self.peer.pk)
        history = DealDetailSerializer(deal).data['stage_history']
        self.assertEqual(history[-1]['actor'], self.actor.pk)
        self.assertEqual(history[-1]['actor_name'], 'Creator')
        legacy = Deal.objects.create(
            deal_code='DEAL-LEGACY', deal_name='Historical opportunity', client=self.client,
            owner=self.peer, estimated_value=Decimal('100'), currency='AED',
            expected_close_date=date(2026, 12, 1),
        )
        legacy_data = DealDetailSerializer(legacy).data
        self.assertIsNone(legacy_data['created_by'])
        self.assertIsNone(legacy_data['stage_history'][-1]['actor'])
        self.assertEqual(legacy_data['stage_history'][-1]['actor_name'], '')

    def test_creator_display_falls_back_to_username_when_full_name_is_blank(self):
        self.actor.first_name = ''
        self.actor.save(update_fields=['first_name'])
        deal = self.register()
        self.assertEqual(DealListSerializer(deal).data['created_by_name'], 'vf-creator')
        detail = DealDetailSerializer(deal).data
        self.assertEqual(detail['created_by_name'], 'vf-creator')
        self.assertEqual(detail['stage_history'][-1]['actor_name'], 'vf-creator')

    def test_detail_and_list_keep_client_and_owner_display_names(self):
        deal = self.register(owner=self.peer.pk)
        for serializer in (DealListSerializer, DealDetailSerializer):
            data = serializer(deal).data
            self.assertEqual(data['client_name'], self.client.company_name)
            self.assertEqual(data['owner_name'], 'Owner')
        self.peer.first_name = ''
        self.peer.save(update_fields=['first_name'])
        deal.refresh_from_db()
        for serializer in (DealListSerializer, DealDetailSerializer):
            self.assertEqual(serializer(deal).data['owner_name'], 'vf-owner')

    def test_allocator_skips_existing_codes_and_preserves_historical_identifiers(self):
        for code in ['Q-102101', 'Q-102102', 'DEAL-ORIGINAL']:
            Deal.objects.create(deal_code=code, deal_name='Historical', client=self.client)
        created = self.register()
        self.assertEqual(created.deal_code, 'Q-102103')
        self.assertTrue(Deal.objects.filter(deal_code='DEAL-ORIGINAL').exists())
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102104)

    def test_committed_number_is_not_reused_after_record_deletion(self):
        created = self.register()
        created.delete()
        self.assertEqual(self.register().deal_code, 'Q-102102')

    def test_failed_save_rolls_back_number_and_all_record_effects(self):
        with patch.object(Deal, 'save', side_effect=RuntimeError('Synthetic save failure')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic save failure'):
                self.register()
        self.assertFalse(Deal.objects.exists())
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102101)
        self.assertEqual(self.register().deal_code, 'Q-102101')

    def test_outer_transaction_failure_rolls_back_allocation(self):
        with self.assertRaisesRegex(RuntimeError, 'Synthetic audit failure'):
            with transaction.atomic():
                self.register()
                raise RuntimeError('Synthetic audit failure')
        self.assertFalse(Deal.objects.exists())
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102101)

    def test_active_team_owner_allowed_and_inactive_or_outside_owner_denied(self):
        self.assertIn(self.peer, visible_opportunity_owners(self.actor))
        self.assertNotIn(self.outsider, visible_opportunity_owners(self.actor))
        self.assertFalse(self.serializer(owner=self.outsider.pk).is_valid())
        self.peer.is_active = False
        self.peer.save(update_fields=['is_active'])
        self.assertFalse(self.serializer(owner=self.peer.pk).is_valid())

    def test_owner_is_rechecked_when_it_becomes_inactive_after_validation(self):
        serializer = self.serializer(owner=self.peer.pk)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.peer.is_active = False
        self.peer.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            serializer.save()
        self.assertEqual(OpportunityNumberSequence.objects.get(pk=1).next_number, 102101)

    def test_unknown_visibility_predicate_fails_closed(self):
        with patch(
            'apps.sales.opportunity_registration.build_visibility_filter',
            return_value=Q(unrecognized_scope=self.actor.pk),
        ):
            self.assertFalse(visible_opportunity_owners(self.actor).exists())

    def test_client_outside_sales_scope_is_rejected_on_create_and_update(self):
        hidden = Client.objects.create(
            client_code='VF-HIDDEN', company_name='Hidden client', industry_type='other',
            account_manager=self.outsider,
        )
        self.assertFalse(self.serializer(client=str(hidden.pk)).is_valid())
        deal = self.register()
        serializer = DealCreateSerializer(
            deal, data={'client': str(hidden.pk)}, partial=True, context={'actor': self.actor},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('client', serializer.errors)

    def test_visible_client_requires_independent_client_read_permission(self):
        existing = self.register()
        RolePermission.objects.filter(
            permission__module__code='sales_clients', permission__action='read',
        ).delete()
        self.assertTrue(module_action_allowed(self.actor, 'sales_opportunities', 'create'))
        self.assertFalse(module_action_allowed(self.actor, 'sales_clients', 'read'))
        self.assertTrue(visible_email_clients(self.actor).filter(pk=self.client.pk).exists())
        create = self.serializer()
        self.assertFalse(create.is_valid())
        self.assertIn('client', create.errors)
        update = DealCreateSerializer(
            existing, data={'client': str(self.client.pk)}, partial=True,
            context={'actor': self.actor},
        )
        self.assertFalse(update.is_valid())
        self.assertIn('client', update.errors)
        self.assertEqual(Deal.objects.count(), 1)

    def test_creator_requires_server_actor_and_invalid_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_registered_opportunity(
                actor=None, validated_data={'deal_name': 'No actor', 'client': self.client},
            )
        self.assertFalse(self.serializer(opportunity_type='invented').is_valid())
        self.assertFalse(Deal.objects.exists())

    def test_client_totals_are_unknown_if_any_included_value_is_missing(self):
        self.assertEqual(ClientListSerializer(self.client).data['total_deal_value'], 0)
        self.register()
        self.assertIsNone(ClientListSerializer(self.client).data['total_deal_value'])
        self.register(estimated_value='150.00', currency='AED')
        data = ClientDetailSerializer(self.client).data['deals_summary']
        self.assertIsNone(data['total_value'])
        self.assertIsNone(data['pipeline_value'])
        Deal.objects.filter(estimated_value__isnull=True).delete()
        data = ClientDetailSerializer(self.client).data['deals_summary']
        self.assertEqual(data['total_value'], Decimal('150.00'))
        self.assertEqual(data['pipeline_value'], Decimal('15.00'))
