"""Read-only email previews and final creation use the same identity rules."""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.hr_core.models import EmployeeMaster
from apps.onboarding.employee_identity import (
    build_employee_identity_preview,
    suggest_employee_email,
    validate_employee_creation_identity,
)


class EmployeeEmailSuggestionTests(SimpleTestCase):
    def test_normalizes_spaces_punctuation_and_accents(self):
        self.assertEqual(suggest_employee_email('  José Luis ', "O'Neill"), 'joseluis.oneill@rejlers.ae')

    def test_missing_or_non_transliterable_names_require_a_manual_address(self):
        self.assertEqual(suggest_employee_email('', 'Smith'), '')
        self.assertEqual(suggest_employee_email('王', '伟'), '')

    def test_long_names_keep_the_email_local_part_within_64_characters(self):
        value = suggest_employee_email('A' * 100, 'B' * 100)
        self.assertEqual(len(value.split('@')[0]), 64)


class EmployeeIdentityPreviewTests(TestCase):
    def user(self, email, first_name='John', last_name='Smith'):
        return get_user_model().objects.create_user(
            username=f'user-{get_user_model().objects.count() + 1}',
            email=email, first_name=first_name, last_name=last_name,
        )

    def employee(self, email, first_name='John', last_name='Smith', user=None):
        number = str(EmployeeMaster.objects.count() + 1)
        return EmployeeMaster.objects.create(
            user=user, employee_number=f'EMP-{number}', employee_code=f'CODE-{number}',
            emp_code=f'BIO-{number}', first_name=first_name, last_name=last_name,
            email=email, join_date=date(2026, 9, 1),
        )

    def test_generated_preview_and_creation_validation_share_the_same_email(self):
        preview = build_employee_identity_preview(' Jane ', ' Doe ')
        self.assertEqual(preview, validate_employee_creation_identity(' Jane ', ' Doe '))
        self.assertEqual(preview['email'], 'jane.doe@rejlers.ae')
        self.assertTrue(preview['available'])
        self.assertEqual(preview['duplicate_count'], 0)
        self.assertEqual(preview['errors'], {})
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(EmployeeMaster.objects.count(), 0)

    def test_custom_email_is_normalized_without_silently_changing_its_account_name(self):
        result = build_employee_identity_preview('Jane', 'Doe', ' J.Doe@REJLERS.AE ')
        self.assertEqual(result['email'], 'j.doe@rejlers.ae')
        self.assertTrue(result['available'])

    def test_user_email_collision_is_case_insensitive_and_suggests_an_unused_address(self):
        self.user('JANE.DOE@rejlers.ae')
        self.employee('jane.doe1@rejlers.ae', first_name='Another')
        result = build_employee_identity_preview('Jane', 'Doe')
        self.assertFalse(result['available'])
        self.assertEqual(result['duplicate_count'], 1)
        self.assertEqual(result['suggested_email'], 'jane.doe2@rejlers.ae')
        self.assertIn('email', result['errors'])
        self.assertEqual(result['duplicates'][0]['match_fields'], ['email'])

    def test_historical_employee_without_user_still_blocks_email_reuse(self):
        self.employee('LEGACY.PERSON@rejlers.ae')
        result = validate_employee_creation_identity('New', 'Person', 'legacy.person@rejlers.ae')
        self.assertFalse(result['available'])
        self.assertEqual(result['duplicate_count'], 1)
        self.assertIn('email', result['errors'])

    def test_linked_user_and_employee_are_counted_as_one_duplicate(self):
        user = self.user('john.smith@rejlers.ae')
        self.employee(user.email, user=user)
        result = build_employee_identity_preview('JOHN', 'SMITH')
        self.assertEqual(result['duplicate_count'], 1)
        self.assertEqual(result['duplicates'][0]['employee_number'], 'EMP-1')
        self.assertEqual(set(result['duplicates'][0]['match_fields']), {'email', 'name'})

    def test_stale_canonical_email_does_not_hide_matching_login_address(self):
        user = self.user('john.smith@rejlers.ae')
        self.employee('j.smith@rejlers.ae', user=user)
        # Model save syncs the canonical address; simulate a historical mismatch
        # with a direct write so both sources must be searched independently.
        get_user_model().objects.filter(pk=user.pk).update(email='john.smith@rejlers.ae')
        result = build_employee_identity_preview('John', 'Smith')
        self.assertFalse(result['available'])
        self.assertEqual(result['duplicate_count'], 1)
        self.assertEqual(result['duplicates'][0]['account_email'], user.email)

    def test_name_only_duplicate_warns_without_blocking_a_distinct_email(self):
        self.employee('john.smith@rejlers.ae')
        result = build_employee_identity_preview('John', 'Smith', 'john.smith2@rejlers.ae')
        self.assertTrue(result['available'])
        self.assertEqual(result['duplicate_count'], 1)
        self.assertEqual(result['duplicates'][0]['match_fields'], ['name'])
        self.assertEqual(result['errors'], {})

    def test_final_validation_rechecks_email_after_a_preview(self):
        preview = build_employee_identity_preview('Jane', 'Doe')
        self.assertTrue(preview['available'])
        self.user(preview['email'])
        submitted = validate_employee_creation_identity('Jane', 'Doe')
        self.assertFalse(submitted['available'])
        self.assertIn('email', submitted['errors'])
        self.assertNotIn('suggested_email', submitted)

    def test_invalid_names_and_emails_produce_field_errors(self):
        result = build_employee_identity_preview('', 'B' * 101, 'not-an-email')
        self.assertFalse(result['available'])
        self.assertEqual(set(result['errors']), {'first_name', 'surname', 'email'})
        for email in ('jane@gmail.com', '.jane@rejlers.ae', 'jane..doe@rejlers.ae', f"{'a' * 65}@rejlers.ae"):
            with self.subTest(email=email):
                self.assertIn('email', build_employee_identity_preview('Jane', 'Doe', email)['errors'])

    def test_non_latin_names_can_use_a_valid_manually_entered_email(self):
        result = build_employee_identity_preview('王', '伟', 'wang.wei@rejlers.ae')
        self.assertTrue(result['available'])
