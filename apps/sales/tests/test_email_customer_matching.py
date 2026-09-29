"""Synthetic canonical matching through current grants, scope and source evidence."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import DatabaseError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.rbac.models import Permission, UserPermissionOverride, UserProfile, UserRole
from apps.sales.email_customer_matching import EmailCustomerMatcher, enrich_customer_match, normalize_customer_name
from apps.sales.email_extraction import extract_email_information
from apps.sales.email_permissions import visible_email_clients
from apps.sales.models import Client

from .access_fixtures import grant_sales_actions


def information(name='Northbridge Utilities Ltd'):
    return extract_email_information(
        subject='RFQ-401 | Engineering study', body_text=f'Customer: {name}\nPlease submit your quotation.',
        sender_email='buyer@example.test', coverage={'status': 'saved_content'},
    )


def client_queries(queries):
    return [query['sql'] for query in queries if 'FROM "sales_clients"' in query['sql']]


class CustomerMatchingFixtures:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.owner = get_user_model().objects.create_user('matching-owner', email='match-owner@example.test')
        self.other = get_user_model().objects.create_user('matching-other', email='match-other@example.test')
        grant_sales_actions(self.owner, 'sales_email_intake', 'sales_clients', 'sales_opportunities')
        grant_sales_actions(self.other, 'sales_email_intake', 'sales_clients', 'sales_opportunities')
        self.outsider = get_user_model().objects.create_user('matching-outsider', email='match-outsider@example.test')
        UserProfile.objects.get_or_create(
            user=self.outsider, defaults={'organization': self.owner.rbac_profile.organization},
        )

    def account(self, name='Northbridge Utilities Ltd', **fields):
        fields.setdefault('client_code', f'SYN-{Client.objects.count() + 1}')
        fields.setdefault('account_manager', self.owner)
        return Client.objects.create(company_name=name, industry_type='other', **fields)

    def deny(self, action='read', user=None):
        permission = Permission.objects.get(module__code='sales_clients', action=action, is_active=True)
        return UserPermissionOverride.objects.create(
            user_profile=(user or self.owner).rbac_profile, permission=permission, allowed=False,
        )


class EmailCustomerMatchingTests(CustomerMatchingFixtures, TestCase):
    def match(self, name='Northbridge Utilities Ltd', *, data=None, user=None):
        return EmailCustomerMatcher(user or self.owner).match(information(name) if data is None else data)

    def test_v2_domain_does_not_replace_explicit_organization_for_matching(self):
        account = self.account()
        data = information()
        data['customer_name'] = data['customer_domain'] = 'unrelated.test'
        result = self.match(data=data)
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['candidates'][0]['id'], str(account.pk))
        self.assertEqual(result['detected_name'], account.company_name)
        data.pop('organization_name')
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.match(data=data)['status'], 'unavailable')
        self.assertEqual(client_queries(queries), [])

    def test_legacy_organization_contract_remains_supported(self):
        self.account()
        data = information()
        data.pop('detection_version')
        data['customer_name'] = data.pop('organization_name')
        data['evidence']['customer_name'] = data['evidence'].pop('organization_name')
        data['field_sources']['customer_name'] = data['field_sources'].pop('organization_name')
        self.assertEqual(self.match(data=data)['status'], 'matched')

    def test_exact_company_legal_and_trading_names_and_same_row_aliases(self):
        account = self.account(legal_name='Northbridge Utilities Ltd', trading_name='Northbridge Trading')
        result = self.match()
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['candidates'][0]['id'], str(account.pk))
        self.assertEqual(result['candidates'][0]['matched_fields'], ['company_name', 'legal_name'])
        self.assertEqual(self.match('Northbridge Trading')['candidates'][0]['matched_fields'], ['trading_name'])
        self.assertTrue(result['needs_review'])
        self.assertFalse(result['has_more'])

    def test_nfc_casefold_and_whitespace_normalize_without_stripping_legal_suffixes(self):
        account = self.account('  STRASSE\t  CAFÉ  LLC ')
        result = self.match('Straße   Cafe\u0301 LLC')
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['candidates'][0]['id'], str(account.pk))
        self.assertEqual(normalize_customer_name('  A & B\t LLC  '), 'a & b llc')
        self.assertEqual(self.match('STRASSE CAFE LLC')['status'], 'no_match')
        self.assertEqual(self.match('STRASSE CAFÉ')['status'], 'no_match')

    def test_punctuation_legal_suffixes_and_domains_are_not_guessed_equivalent(self):
        self.account('A & B LLC', email='buyer@example.test', website='https://example.test')
        for name in ('A and B LLC', 'A & B Limited', 'A-B LLC', 'example.test'):
            with self.subTest(name=name):
                self.assertEqual(self.match(name)['status'], 'no_match')

    def test_duplicate_names_on_distinct_clients_remain_ambiguous(self):
        first = self.account(legal_name='Northbridge Utilities Ltd')
        second = self.account('Northbridge Holdings', trading_name='Northbridge Utilities Ltd')
        result = self.match()
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual({item['id'] for item in result['candidates']}, {str(first.pk), str(second.pk)})
        self.assertEqual(len(result['candidates']), 2)

    def test_twenty_candidate_cap_keeps_true_ambiguity_and_has_more(self):
        for number in range(23):
            self.account(client_code=f'DUP-{number:03d}')
        result = self.match()
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(len(result['candidates']), 20)
        self.assertTrue(result['has_more'])
        self.assertEqual(len({item['id'] for item in result['candidates']}), 20)

    def test_match_after_first_500_authorized_records_is_not_lost(self):
        Client.objects.bulk_create([
            Client(client_code=f'EARLY-{number:04d}', company_name=f'Aardvark {number:04d}',
                   account_manager=self.owner, industry_type='other')
            for number in range(550)
        ])
        target = self.account('Zebra Utilities Ltd')
        result = self.match('Zebra Utilities Ltd')
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['candidates'][0]['id'], str(target.pk))

    def test_hidden_exact_name_is_indistinguishable_from_no_matching_client(self):
        before = self.match()
        hidden = self.account(account_manager=self.outsider)
        self.assertFalse(visible_email_clients(self.owner).filter(pk=hidden.pk).exists())
        self.assertEqual(self.match(), before)
        self.assertEqual(before['status'], 'no_match')
        self.assertNotIn(str(hidden.pk), str(before))

    def test_hidden_duplicate_does_not_change_single_visible_match_or_has_more(self):
        visible = self.account()
        self.account(account_manager=self.outsider)
        result = self.match()
        self.assertEqual(result['status'], 'matched')
        self.assertFalse(result['has_more'])
        self.assertEqual([item['id'] for item in result['candidates']], [str(visible.pk)])

    def test_no_request_anonymous_inactive_and_denied_never_scan_clients(self):
        self.account()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(EmailCustomerMatcher(None).match(information())['status'], 'unavailable')
            self.assertEqual(EmailCustomerMatcher(AnonymousUser()).match(information())['status'], 'denied')
            self.owner.is_active = False
            self.assertEqual(EmailCustomerMatcher(self.owner).match(information())['status'], 'denied')
        self.assertEqual(client_queries(queries), [])
        self.owner.is_active = True
        self.deny()
        with CaptureQueriesContext(connection) as queries:
            denied = self.match()
        self.assertEqual(denied['status'], 'denied')
        self.assertEqual(denied['candidates'], [])
        self.assertEqual(denied['detected_name'], '')
        self.assertEqual(client_queries(queries), [])

    def test_read_deny_wins_for_admin_and_after_prior_success(self):
        self.account()
        admin = get_user_model().objects.create_superuser('matching-admin', email='match-admin@example.test', password='synthetic-only')
        grant_sales_actions(admin, 'sales_clients')
        self.assertEqual(self.match(user=admin)['status'], 'matched')
        self.deny(user=admin)
        with CaptureQueriesContext(connection) as queries:
            result = self.match(user=admin)
        self.assertEqual(result['status'], 'denied')
        self.assertEqual(client_queries(queries), [])

    def test_new_request_rechecks_ownership_and_does_not_reuse_other_response_index(self):
        account = self.account()
        first = enrich_customer_match(information(), request=SimpleNamespace(user=self.owner))
        self.assertEqual(first['customer_match']['status'], 'matched')
        account.account_manager = self.outsider
        account.save(update_fields=['account_manager'])
        second = enrich_customer_match(information(), request=SimpleNamespace(user=self.owner))
        self.assertEqual(second['customer_match']['status'], 'no_match')

    def test_missing_conflicting_and_unreferenced_customer_never_scan_clients(self):
        missing = extract_email_information(subject='Hello', body_text='Please call me.', sender_email='buyer@example.test')
        conflicting = extract_email_information(subject='RFQ', body_text='Customer: First Company\nCustomer: Second Company')
        variants = [
            (missing, 'not_detected'), (conflicting, 'conflicting'),
            ({**information(), 'field_sources': {'organization_name': ['missing-source']}}, 'unavailable'),
            ({**information(), 'evidence': {}}, 'unavailable'),
            ({**information(), 'organization_name': 'Unevidenced company'}, 'unavailable'),
            ({**information(), 'organization_name': {'unsafe': 'value'}}, 'unavailable'),
            ({}, 'unavailable'),
        ]
        for data, status in variants:
            with self.subTest(status=status), CaptureQueriesContext(connection) as queries:
                result = self.match(data=data)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['candidates'], [])
            self.assertEqual(client_queries(queries), [])

    def test_conflict_warning_does_not_allow_a_nonblank_fallback_name_to_match(self):
        self.account()
        data = information()
        data['warnings'] = ['Conflicting customer_name evidence in the available chain needs review.']
        self.assertEqual(self.match(data=data)['status'], 'conflicting')

    def test_one_request_scans_directory_once_for_distinct_names_and_returns_safe_fields(self):
        self.account(notes='synthetic private note', email='private@example.test', annual_revenue='12345.67')
        self.account('Other Utilities')
        request = SimpleNamespace(user=self.owner)
        with CaptureQueriesContext(connection) as queries:
            first = enrich_customer_match(information(), request=request)
            second = enrich_customer_match(information('Other Utilities'), request=request)
        scans = client_queries(queries)
        self.assertEqual(len(scans), 1)
        self.assertNotIn('annual_revenue', scans[0])
        self.assertNotIn('notes', scans[0])
        self.assertEqual(first['customer_match']['status'], 'matched')
        self.assertEqual(second['customer_match']['status'], 'matched')
        candidate = first['customer_match']['candidates'][0]
        self.assertEqual(set(candidate), {'id', 'client_code', 'company_name', 'matched_fields', 'status', 'verification_status', 'new_proposals_permitted'})
        self.assertNotIn('synthetic private note', str(first))

    def test_request_user_switch_rebuilds_instead_of_reusing_another_users_index(self):
        account = self.account(account_manager=self.outsider)
        admin = get_user_model().objects.create_superuser('matching-switch-admin', email='switch-admin@example.test', password='synthetic-only')
        grant_sales_actions(admin, 'sales_clients')
        request = SimpleNamespace(user=admin)
        self.assertEqual(enrich_customer_match(information(), request=request)['customer_match']['status'], 'matched')
        request.user = self.owner
        result = enrich_customer_match(information(), request=request)['customer_match']
        self.assertEqual(result['status'], 'no_match')
        self.assertNotIn(str(account.pk), str(result))

    def test_current_sales_team_visibility_and_removed_owner_role_are_rechecked(self):
        account = self.account(account_manager=self.other)
        self.assertTrue(visible_email_clients(self.owner).filter(pk=account.pk).exists())
        self.assertEqual(self.match()['status'], 'matched')
        UserRole.objects.filter(user_profile=self.other.rbac_profile).delete()
        self.assertFalse(visible_email_clients(self.owner).filter(pk=account.pk).exists())
        self.assertEqual(self.match()['status'], 'no_match')

    def test_interrupted_directory_scan_discards_partial_matches(self):
        row = {
            'id': 'synthetic-id', 'client_code': 'SYN', 'company_name': 'Northbridge Utilities Ltd',
            'legal_name': '', 'trading_name': '', 'status': 'prospect',
            'verification_status': 'unverified', 'new_proposals_permitted': True,
        }

        def interrupted(*args, **kwargs):
            yield row
            raise DatabaseError('synthetic private database detail')

        with patch('apps.sales.email_customer_matching.visible_email_clients') as queryset:
            queryset.return_value.order_by.return_value.values.return_value.iterator.side_effect = interrupted
            result = self.match()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['candidates'], [])
        self.assertNotIn('synthetic private', str(result))

    def test_matches_are_informational_even_for_inactive_restricted_clients(self):
        self.account(status='inactive', verification_status='restricted', new_proposals_permitted=False)
        result = self.match()
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['candidates'][0]['status'], 'inactive')
        self.assertFalse(result['candidates'][0]['new_proposals_permitted'])
        self.assertTrue(result['needs_review'])

    def test_response_mutation_cannot_change_shared_index_or_original_extraction(self):
        self.account()
        data = information()
        before = deepcopy(data)
        request = SimpleNamespace(user=self.owner)
        response = enrich_customer_match(data, request=request)
        response['customer_match']['candidates'][0]['matched_fields'].append('forged-field')
        response['customer_match']['candidates'][0]['company_name'] = 'Forged name'
        repeated = enrich_customer_match(data, request=request)
        self.assertEqual(repeated['customer_match']['candidates'][0]['matched_fields'], ['company_name'])
        self.assertEqual(data, before)
