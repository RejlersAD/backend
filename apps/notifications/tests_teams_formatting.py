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
        self.assertNotIn('Description:', payload['message'])
        self.assertNotIn(VISIBLE_DESCRIPTION, payload['message'])
        self.assertIn('Service: Supply of InfoMaker Standard Edition', payload['message'])
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

    def test_card_keeps_user_punctuation_literal_in_text_runs(self):
        context = {
            'request_name': 'Purchase Order RAD-PRJ-PUR-0480_2026',
            'po_number': 'RAD-PRJ-PUR-0480_2026',
            'description': '*Vendor* [portal](https://supplier.example.test)\nReview scope.',
        }
        payload = build_approval_assignment_payload(self.notification(), context)
        facts = {
            row['inlines'][0]['text'].removesuffix(': '): row['inlines'][1]['text']
            for row in payload['attachments'][0]['content']['body'][1:]
        }
        self.assertEqual(payload['po_number'], 'RAD-PRJ-PUR-0480_2026')
        self.assertIn('RAD-PRJ-PUR-0480_2026', payload['message'])
        self.assertEqual(facts['PO Number'], context['po_number'])
        self.assertEqual(facts['Description'], context['description'])
        self.assertEqual(payload['description'], context['description'])

    def test_compact_messages_emphasize_labels_and_key_purchase_order_details(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'title': 'New purchase order created',
            'po_number': 'RAD-PRJ-PUR-0480_2026',
            'project_id': '5901205', 'value': 'AED 89,512.50',
            'approval_level': 0,
        })
        self.assertNotIn('\n\n', payload['message'])
        html = payload['message_html']
        self.assertEqual(html.count('<p>'), 1)
        self.assertEqual(html.count('</p>'), 1)
        self.assertNotIn('<br><br>', html)
        for line in (
            '<b>New purchase order created</b>',
            '<b>PO Number: RAD-PRJ-PUR-0480_2026</b>',
            '<b>Project Code: 5901205</b>', '<b>Value: AED 89,512.50</b>',
            '<b>Approval Level:</b> Level 0', '<b>Submitted By:</b> Firaol Akawak Nemomsa',
        ):
            self.assertIn(line, html)
        for row in payload['attachments'][0]['content']['body'][1:]:
            self.assertEqual(row['spacing'], 'None')
            label, value = row['inlines']
            self.assertEqual(label['weight'], 'Bolder')
            emphasized = label['text'] in {'PO Number: ', 'Project Code: ', 'Value: '}
            self.assertEqual(value['weight'], 'Bolder' if emphasized else 'Default')

    def test_html_preview_escapes_text_and_link_without_hiding_visible_engineering_notation(self):
        notification = self.notification()
        notification.action_url = '/procurement/orders/po-1?mode="view"&source=teams'
        payload = build_approval_assignment_payload(notification, {
            'description': 'Use <DN50> pipe & "special" fittings.\nIssue drawings.',
        })
        html = payload['message_html']
        self.assertIn('Use &lt;DN50&gt; pipe &amp; &quot;special&quot; fittings.<br>Issue drawings.', html)
        self.assertNotIn('<DN50>', html)
        self.assertIn('href="https://www.radai.ae/procurement/orders/po-1?mode=&quot;view&quot;&amp;source=teams"', html)
        self.assertNotIn('Approval Level:', html)

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

    def test_procurement_approval_matches_short_reference_without_blank_rows(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'entity_type': 'purchase_order',
            'request_number': 'RAD-PRJ-PUR-0477_2026',
            'po_number': 'RAD-PRJ-PUR-0477_2026',
            'project_id': '5901056',
            'project_name': 'Detailed Engineering Services for Central Engineering Division Remote Control Center',
            'service': 'Telecom Integrator Consultancy Services',
            'vendor': 'Exctel Engineering Pte Ltd',
            'value': 'USD 236,888.40',
            'description': COPIED_DESCRIPTION, 'approval_level': 0,
        })
        lines = payload['message'].splitlines()
        self.assertEqual(lines[:7], [
            '🚨 NEW PO – APPROVAL REQUIRED',
            'PR/PO: RAD-PRJ-PUR-0477_2026',
            'Project: 5901056 – Detailed Engineering Services for Central Engineering Division Remote Control Center',
            'Service: Telecom Integrator Consultancy Services',
            'Vendor: Exctel Engineering Pte Ltd',
            'Value: USD 236,888.40',
            '⌛ Due for approval: Not specified',
        ])
        self.assertEqual(len(lines), 8)
        self.assertTrue(lines[7].startswith('Open Request: https://www.radai.ae/'))
        markup = payload['message_html']
        self.assertEqual(markup.count('<br>'), 7)
        self.assertNotIn('<br><br>', markup)
        self.assertIn('<b>PR/PO:</b> RAD-PRJ-PUR-0477_2026', markup)
        self.assertIn('<b>Value: USD 236,888.40</b>', markup)
        self.assertNotIn('Description:', markup)
        self.assertNotIn('Submitted By:', markup)
        self.assertNotIn('Approval Level:', markup)
        card = payload['attachments'][0]['content']
        self.assertEqual(len(card['body']), 7)
        self.assertEqual(card['body'][0]['text'], lines[0])
        self.assertEqual(card['body'][0]['size'], 'Default')
        for row, line in zip(card['body'][1:], lines[1:7]):
            self.assertEqual(row['spacing'], 'None')
            self.assertEqual(''.join(run['text'] for run in row['inlines']), line)

    def test_pr_keeps_own_number_and_explicit_approval_timestamp(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'entity_type': 'purchase_recommendation', 'request_number': 'PR-123',
            'po_number': 'RELATED-PO-456', 'approval_due_at': '2026-09-25T16:30:00+04:00',
        })
        self.assertEqual(payload['title'], '🚨 NEW PR – APPROVAL REQUIRED')
        self.assertIn('PR/PO: PR-123\n', payload['message'])
        self.assertNotIn('RELATED-PO-456', payload['message'])
        self.assertEqual(payload['po_number'], 'RELATED-PO-456')
        self.assertIn('⌛ Due for approval: 25 Sep 2026, 16:30 UTC+0400', payload['message'])

    def test_missing_pr_number_does_not_fall_back_to_related_po(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'entity_type': 'purchase_recommendation', 'po_number': 'RELATED-PO-456',
        })
        self.assertIn('PR/PO: Not specified\n', payload['message'])
        self.assertNotIn('RELATED-PO-456', payload['message'])

    def test_procurement_summary_flattens_and_bounds_long_rich_fields(self):
        context = {
            'entity_type': 'purchase_order', 'po_number': 'PO-123',
            'project_id': '5901056', 'project_name': 'Project name ' * 40,
            'service': '<p>Design &amp; review</p><p>' + 'Engineering services ' * 100 + '</p>',
            'vendor': '<p>' + 'Supplier ' * 100 + '</p>',
            'description': COPIED_DESCRIPTION,
        }
        original = deepcopy(context)
        payload = build_approval_assignment_payload(self.notification(), context)
        self.assertEqual(context, original)
        self.assertEqual(len(payload['message'].splitlines()), 8)
        rows = payload['attachments'][0]['content']['body'][1:]
        facts = {row['inlines'][0]['text']: row['inlines'][1]['text'] for row in rows}
        for label, limit in (('Service: ', 180), ('Project: ', 220), ('Vendor: ', 160)):
            self.assertLessEqual(len(facts[label]), limit)
            self.assertTrue(facts[label].endswith('…'))
            self.assertNotIn('\n', facts[label])
        self.assertIn('Design &amp; review Engineering services', payload['message_html'])
        self.assertGreater(len(payload['service']), len(facts['Service: ']))

    def test_buyer_fyi_has_compact_fields_without_approval_demand(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'entity_type': 'purchase_order', 'po_number': 'PO-123',
            'event_type': 'purchase_order_created', 'title': 'New purchase order created',
        })
        self.assertEqual(payload['title'], 'New purchase order created')
        self.assertEqual(len(payload['message'].splitlines()), 7)
        self.assertNotIn('APPROVAL REQUIRED', payload['message'])
        self.assertNotIn('Due for approval', payload['message_html'])

    def test_procurement_summary_keeps_engineering_notation_and_escapes_html(self):
        payload = build_approval_assignment_payload(self.notification(), {
            'entity_type': 'purchase_order', 'po_number': 'PO_42',
            'service': 'Use <DN50> pipe & "special" fittings.\nIssue drawings.',
            'vendor': '<b>Supplier &amp; Co</b><script>hidden()</script>',
        })
        self.assertIn('Use &lt;DN50&gt; pipe &amp; &quot;special&quot; fittings. Issue drawings.', payload['message_html'])
        self.assertNotIn('hidden()', payload['message_html'])
        service = payload['attachments'][0]['content']['body'][3]['inlines'][1]['text']
        self.assertEqual(service, 'Use <DN50> pipe & "special" fittings. Issue drawings.')

    def test_metadata_only_buyer_fyi_is_not_mistaken_for_an_assignment(self):
        notice = self.notification()
        notice.metadata = {
            'entity_type': 'purchase_order', 'request_number': 'PO-123',
            'event_type': 'po_created',
        }
        payload = build_approval_assignment_payload(notice)
        self.assertEqual(payload['event_type'], 'po_created')
        self.assertEqual(payload['title'], 'New purchase order created')
        self.assertIn('PR/PO: PO-123', payload['message'])
        self.assertNotIn('APPROVAL REQUIRED', payload['message'])
        self.assertNotIn('Due for approval', payload['message_html'])

    def test_missing_or_invalid_approval_timestamp_never_becomes_a_deadline(self):
        for deadline in (None, '', '2026-09-25', '2026-09-25T16:30:00', '2026-09-99T16:30:00+04:00'):
            with self.subTest(deadline=deadline):
                payload = build_approval_assignment_payload(self.notification(), {
                    'entity_type': 'purchase_order', 'approval_due_at': deadline,
                    'due_date': '2026-09-26',
                })
                self.assertIn('⌛ Due for approval: Not specified', payload['message'])
