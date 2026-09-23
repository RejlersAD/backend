"""Regression coverage for rich procurement descriptions delivered to Teams."""

from copy import deepcopy
from decimal import Decimal
from html import escape
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from apps.notifications.teams import build_approval_assignment_payload
from apps.notifications.teams_formatting import teams_plain_text
from apps.procurement.services.notification_context import (
    purchase_order_teams_context,
    requisition_teams_context,
)


VISIBLE_DESCRIPTION = (
    'Based on your active interests in programming, digital content creation, '
    'and your recently tracked tech upgrades, here are tailored purchase '
    'recommendations available right now in the UAE:'
)
COPIED_DESCRIPTION = (
    '<div class="n6owBd awi2gc" data-sfc-cp="" jsaction="" '
    'jscontroller="TDBkbc#Ml18Xb" jsuid="hWElYc_t" data-hveid="CAAIEBAA" '
    'data-copy-service-computed-style="font-family: &quot;Google Sans&quot;, Arial; '
    'font-size: 16px; border-bottom: 0px rgb(230, 232, 240);" '
    'style="font-family: &quot;Google Sans&quot;, Arial; margin: 12px 0px 16px;">'
    '<span data-subtree="aimfl" data-processed="true" '
    'style="border-bottom: 0px rgb(230, 232, 240);">'
    f'{VISIBLE_DESCRIPTION}</span><!--TgQPHd|||[]--></div>'
    '<div class="yhAwj" jsaction="rcuQ6b:&amp;hWElYc_10|npT2md" '
    'jscontroller="UTzWVc#U8DOt"><!--TgQPHd|||[]--></div>'
    '<div class="otQkpb" aria-level="3" role="heading" '
    'data-animation-nesting="" data-sfc-root="ep" '
    'style="font-size: 20px; font-weight: 600;"></div>'
)


class TeamsPlainTextTests(SimpleTestCase):
    def test_copied_purchase_order_html_becomes_only_visible_prose(self):
        self.assertEqual(teams_plain_text(COPIED_DESCRIPTION), VISIBLE_DESCRIPTION)

    def test_escaped_and_double_escaped_html_are_readable(self):
        source = '<p>Supply &amp; install&nbsp;InfoMaker &#8212; 1 licence.</p>'
        for value in (source, escape(source), escape(escape(source))):
            with self.subTest(value=value):
                self.assertEqual(
                    teams_plain_text(value), 'Supply & install InfoMaker — 1 licence.',
                )

    def test_paragraphs_and_line_breaks_do_not_join_words(self):
        self.assertEqual(
            teams_plain_text('<p>Survey area<br>Review drawings</p><p>Issue package</p>'),
            'Survey area\nReview drawings\nIssue package',
        )

    def test_list_and_table_content_remains_separated_and_in_order(self):
        text = teams_plain_text(
            '<ul><li>Design review</li><li>Vendor evaluation</li></ul>'
            '<table><tr><th>Item</th><th>Quantity</th></tr>'
            '<tr><td>Licence</td><td>3 seats</td></tr></table>'
        )
        expected = ('Design review', 'Vendor evaluation', 'Item', 'Quantity', 'Licence', '3 seats')
        positions = [text.index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('reviewVendor', text)
        self.assertNotIn('ItemQuantity', text)
        self.assertNotIn('Licence3', text)
        self.assertNotIn('<', text)

    def test_script_style_template_and_comments_are_not_description_content(self):
        source = (
            '<div>Scope: supply licence.</div>'
            '<script>window.secret = "do not show";</script>'
            '<style>.secret { color: red; }</style>'
            '<template><div>Hidden template content</div></template>'
            '<!-- invisible annotation --><p>Includes support.</p>'
        )
        self.assertEqual(teams_plain_text(source), 'Scope: supply licence.\nIncludes support.')

    def test_empty_rich_text_uses_the_requested_fallback(self):
        for value in (None, '', '  ', '<p><br>&nbsp;</p>', '<!-- copied -->', '<script>x()</script>'):
            with self.subTest(value=value):
                self.assertEqual(teams_plain_text(value), 'Not specified')
                self.assertEqual(teams_plain_text(value, default='Not issued'), 'Not issued')
                self.assertEqual(teams_plain_text(value, default=''), '')

    def test_engineering_comparisons_unicode_and_plain_identifiers_are_preserved(self):
        for value in (
            '2<5 & 6>3; ΔP ≤ 2.5 bar — دبي; RAD-PRJ-PUR-0480_2026',
            'P<MAWP; T>Tmax; pressure<rated',
            'Grade <S355> steel and <DN50> pipe',
        ):
            with self.subTest(value=value):
                self.assertEqual(teams_plain_text(value), value)
        self.assertEqual(teams_plain_text('<p>Use &lt;DN50&gt; pipe</p>'), 'Use <DN50> pipe')

    def test_malformed_hidden_markup_recovers_at_the_parent_boundary(self):
        self.assertEqual(
            teams_plain_text('<div>Lead<span hidden>secret</div><p>Visible recovery</p>'),
            'Lead\nVisible recovery',
        )

    def test_custom_editor_wrappers_with_attributes_are_removed(self):
        self.assertEqual(
            teams_plain_text('<editor-widget data-copy-style="font-weight: 600">Scope.</editor-widget>'),
            'Scope.',
        )

    def test_word_namespace_wrappers_do_not_leak_into_visible_text(self):
        self.assertEqual(
            teams_plain_text('<p class="MsoNormal">Review<o:p>&nbsp;</o:p></p>'),
            'Review',
        )

    def test_whitespace_and_nonbreaking_spaces_are_normalized(self):
        self.assertEqual(
            teams_plain_text('<p>  Supply\t and\r\n  install&nbsp;&nbsp;licences. </p>\n\n<p>Support.</p>'),
            'Supply and install licences.\nSupport.',
        )

    def test_saved_plain_text_line_breaks_are_preserved(self):
        text = 'Scope: supply licence.\nIncludes support.\nIssue vendor documentation.'
        self.assertEqual(teams_plain_text(text), text)

    def test_length_limit_applies_after_markup_is_removed(self):
        self.assertEqual(
            teams_plain_text(f'<p data-copied-style="{"x" * 5000}">Short scope.</p>'),
            'Short scope.',
        )
        text = teams_plain_text('<p>' + 'Engineering verification work. ' * 200 + '</p>')
        self.assertLessEqual(len(text), 1500)
        self.assertTrue(text.endswith('… (Open Request for full text)'))
        self.assertTrue(text.startswith('Engineering verification work.'))
        self.assertNotIn('<p>', text)


@override_settings(FRONTEND_URL='https://www.radai.ae')
class TeamsFormattedPayloadTests(SimpleTestCase):
    def notification(self):
        return SimpleNamespace(
            pk='notification_0480',
            title='PO approval',
            recipient=SimpleNamespace(
                email='approver+po@example.test', username='approver',
                get_full_name=lambda: '<b>Approver &amp; Reviewer</b>',
            ),
            sender=SimpleNamespace(
                email='requester@example.test', username='requester',
                get_full_name=lambda: '<span>Firaol Akawak Nemomsa</span>',
            ),
            action_url='/procurement/orders/c5c0cf7d-e206-44e9-a29a-c38cb9f5ad3e?mode=view&source=teams',
            action_label='<span>Open Request</span>',
        )

    def test_actual_po_context_produces_readable_message_without_changing_saved_description(self):
        order = SimpleNamespace(
            po_number='RAD-PRJ-PUR-0480_2026',
            enterprise_project=SimpleNamespace(
                name='AS BUILT RECOVERY & VIRTUAL TOUR PHASE 3', code='5901205',
            ),
            title='Supply of InfoMaker Standard Edition, Annual Subscription License (1 no)',
            description=COPIED_DESCRIPTION,
            vendor=SimpleNamespace(name='Emirates Technical & Safety Development'),
            currency='AED', total_amount=Decimal('89512.50'),
        )
        context = purchase_order_teams_context(order)
        context.update(event_type='purchase_order_created', title='New purchase order created')
        original_context = deepcopy(context)
        payload = build_approval_assignment_payload(self.notification(), context)

        self.assertEqual(payload['description'], VISIBLE_DESCRIPTION)
        self.assertEqual(payload['po_number'], 'RAD-PRJ-PUR-0480_2026')
        self.assertEqual(payload['value'], 'AED 89,512.50')
        self.assertEqual(payload['project_id'], '5901205')
        self.assertIn(f'Description: {VISIBLE_DESCRIPTION}', payload['message'])
        self.assertNotIn('data-copy-service', payload['message'])
        self.assertNotIn('TgQPHd', payload['message'])
        self.assertEqual(order.description, COPIED_DESCRIPTION)
        self.assertEqual(context, original_context)

    def test_pr_description_fallback_is_cleaned_without_changing_original_source(self):
        pr = SimpleNamespace(
            pr_number='RAD-PRJ-PR-0426_2026',
            product_service='<p>Annual software subscription<br>Includes support.</p>',
            description_reason='',
            supplier_name='<span>Emirates &amp; Technical</span>',
            total_price=Decimal('100.00'), currency='AED',
        )
        context = requisition_teams_context(pr)
        original_context = deepcopy(context)
        original_source = pr.product_service
        payload = build_approval_assignment_payload(self.notification(), context)

        self.assertEqual(payload['description'], 'Annual software subscription\nIncludes support.')
        self.assertEqual(payload['service'], payload['description'])
        self.assertEqual(payload['vendor'], 'Emirates & Technical')
        self.assertEqual(payload['po_number'], 'Not issued')
        self.assertIn('<p>', pr.product_service)
        self.assertEqual(pr.product_service, original_source)
        self.assertEqual(context, original_context)

    def test_po_scope_fallback_is_cleaned_at_delivery_boundary(self):
        order = SimpleNamespace(
            po_number='PO_SCOPE_2026',
            scope_of_services='<p>Collect inputs.</p><p>Develop design.<br>Issue drawings.</p>',
        )
        context = purchase_order_teams_context(order)
        original_context = deepcopy(context)
        original_source = order.scope_of_services
        payload = build_approval_assignment_payload(self.notification(), context)

        self.assertEqual(payload['description'], 'Collect inputs.\nDevelop design.\nIssue drawings.')
        self.assertEqual(payload['service'], payload['description'])
        self.assertEqual(context, original_context)
        self.assertEqual(order.scope_of_services, original_source)
        self.assertIn('<p>', order.scope_of_services)

    def test_empty_rich_text_does_not_mask_available_procurement_fallbacks(self):
        po = SimpleNamespace(
            po_number='PO-EMPTY-DESCRIPTION',
            description='<p>&nbsp;<br></p>',
            scope_of_services='<p>Actual scope of services.</p>',
        )
        pr = SimpleNamespace(
            pr_number='PR-EMPTY-DESCRIPTION',
            description_reason='<div><br></div>',
            product_service='<p>Actual procurement scope.</p>',
        )
        for context, expected in (
            (purchase_order_teams_context(po), 'Actual scope of services.'),
            (requisition_teams_context(pr), 'Actual procurement scope.'),
        ):
            with self.subTest(expected=expected):
                payload = build_approval_assignment_payload(self.notification(), context)
                self.assertEqual(payload['description'], expected)
        self.assertEqual(po.description, '<p>&nbsp;<br></p>')
        self.assertEqual(pr.description_reason, '<div><br></div>')

    def test_all_display_fields_are_cleaned_but_routing_and_contract_keys_are_preserved(self):
        context = {
            'event_type': 'purchase_order_created',
            'title': '<b>New purchase order created</b>',
            'request_name': '<b>Purchase Order PO_42</b>',
            'description': '<p>Engineering &amp; design</p>',
            'project_name': '<div>Project Alpha</div>',
            'project_id': '<span>5901205</span>',
            'po_number': '<em>PO_42</em>',
            'service': '<p>Design review</p>',
            'vendor': '<b>Supplier LLC</b>',
            'value': '<span>AED 100.00</span>',
            'currency': '<span>AED</span>',
        }
        payload = build_approval_assignment_payload(self.notification(), context)
        expected = {
            'title': 'New purchase order created', 'request': 'Purchase Order PO_42',
            'description': 'Engineering & design', 'project_name': 'Project Alpha',
            'project_id': '5901205', 'po_number': 'PO_42', 'service': 'Design review',
            'vendor': 'Supplier LLC', 'value': 'AED 100.00', 'currency': 'AED',
            'submitted_by': 'Firaol Akawak Nemomsa', 'recipient_name': 'Approver & Reviewer',
            'action_label': 'Open Request',
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(payload[key], value)
        self.assertEqual(payload['notification_id'], 'notification_0480')
        self.assertEqual(payload['recipient_email'], 'approver+po@example.test')
        self.assertEqual(payload['event_type'], 'purchase_order_created')
        self.assertEqual(
            payload['action_url'],
            'https://www.radai.ae/procurement/orders/'
            'c5c0cf7d-e206-44e9-a29a-c38cb9f5ad3e?mode=view&source=teams',
        )
        self.assertEqual(
            payload['attachments'][0]['content']['actions'][0]['url'], payload['action_url'],
        )

    def test_card_escapes_markdown_without_changing_plain_purchase_order_number(self):
        context = {
            'request_name': 'Purchase Order RAD-PRJ-PUR-0480_2026',
            'po_number': 'RAD-PRJ-PUR-0480_2026',
            'description': '*Vendor* [portal](https://supplier.example.test)\nReview scope.',
        }
        payload = build_approval_assignment_payload(self.notification(), context)
        facts = {
            fact['title']: fact['value']
            for fact in payload['attachments'][0]['content']['body'][1]['facts']
        }
        self.assertEqual(payload['po_number'], 'RAD-PRJ-PUR-0480_2026')
        self.assertIn('RAD-PRJ-PUR-0480_2026', payload['message'])
        self.assertIn(r'0480\_2026', facts['PO Number'])
        self.assertIn(r'\*Vendor\*', facts['Description'])
        self.assertIn(r'\[portal\]', facts['Description'])
        self.assertEqual(payload['description'], context['description'])

    def test_empty_markup_receives_semantic_fallbacks(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'po_number': '<p><br></p>',
            'description': '<p>&nbsp;</p>',
            'vendor': '<!-- copied vendor -->',
            'submitted_by': '<span>Requester</span>',
        })
        self.assertEqual(payload['po_number'], 'Not issued')
        self.assertEqual(payload['description'], 'Not specified')
        self.assertEqual(payload['vendor'], 'Not specified')
        self.assertEqual(payload['submitted_by'], 'Requester')

    def test_description_and_service_lengths_are_bounded_independently(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'description': '<p>' + 'Design and verify equipment. ' * 250 + '</p>',
            'service': '<p>' + 'Engineering service. ' * 100 + '</p>',
            'vendor': '<b>' + 'Supplier ' * 100 + '</b>',
        })
        self.assertLessEqual(len(payload['description']), 1500)
        self.assertLessEqual(len(payload['service']), 700)
        self.assertLessEqual(len(payload['vendor']), 300)
        self.assertTrue(payload['description'].endswith('… (Open Request for full text)'))
