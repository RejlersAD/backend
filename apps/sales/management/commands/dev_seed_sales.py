"""Seed a coherent, end-to-end Sales lifecycle for local demonstrations.

Usage:
    python manage.py dev_seed_sales user@example.com
    python manage.py dev_seed_sales user@example.com --reset

The command is idempotent and refuses to run outside a DEBUG, non-production
environment. All seeded business identifiers start with ``DEMO-``.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.project_models import Project, ProjectMember
from apps.sales.models import (
    Client,
    Contact,
    Deal,
    FrameworkAgreement,
    OpportunityAuditEvent,
    ProjectHandover,
    Quote,
    SalesActivity,
    SalesForecast,
)


DEMO_PREFIX = 'DEMO-'
HANDOVER_CHECKLIST = {
    'signed_contract': True,
    'final_scope': True,
    'client_contacts': True,
    'contract_value': True,
    'approved_hours_and_budget': True,
    'billing_milestones': True,
    'payment_terms': True,
    'risks_and_mitigations': True,
    'project_manager': True,
}


class Command(BaseCommand):
    help = 'DEV-ONLY: seed an end-to-end consulting Sales demonstration dataset.'

    def add_arguments(self, parser):
        parser.add_argument('email', help='User who will own and see the demo Sales records')
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Remove existing DEMO Sales records before rebuilding them',
        )

    def handle(self, *args, **options):
        environment = str(getattr(settings, 'ENVIRONMENT', '')).lower()
        if environment == 'production' or not getattr(settings, 'DEBUG', False):
            raise CommandError(
                f'REFUSED: dev_seed_sales is development-only '
                f'(ENVIRONMENT={environment or "unknown"}, DEBUG={settings.DEBUG}).'
            )

        User = get_user_model()
        try:
            owner = User.objects.get(email__iexact=options['email'], is_active=True)
        except User.DoesNotExist as exc:
            raise CommandError(f'Active user not found: {options["email"]}') from exc

        approver = (
            User.objects.filter(is_active=True, is_superuser=True)
            .exclude(pk=owner.pk)
            .order_by('first_name', 'last_name', 'email')
            .first()
            or User.objects.filter(is_active=True).exclude(pk=owner.pk).order_by('email').first()
            or owner
        )

        with transaction.atomic():
            if options['reset']:
                self._reset()
            counts = self._seed(owner, approver)

        self.stdout.write(self.style.SUCCESS('Sales demonstration dataset is ready.'))
        self.stdout.write(
            '  ' + ', '.join(f'{name}: {count}' for name, count in counts.items())
        )
        self.stdout.write(f'  Owner: {owner.email}')
        self.stdout.write('  Open: http://localhost:5173/sales')

    def _reset(self):
        demo_deals = Deal.objects.filter(deal_code__startswith=DEMO_PREFIX)
        demo_projects = Project.objects.filter(code__startswith='DEMO-SALES-PRJ-')

        ProjectHandover.objects.filter(opportunity__in=demo_deals).delete()
        demo_deals.update(converted_project=None, converted_by=None, converted_at=None)
        demo_projects.delete()
        SalesActivity.objects.filter(deal__in=demo_deals).delete()
        Quote.objects.filter(quote_number__startswith=DEMO_PREFIX).delete()
        OpportunityAuditEvent.objects.filter(opportunity__in=demo_deals).delete()
        demo_deals.delete()
        FrameworkAgreement.objects.filter(framework_number__startswith=DEMO_PREFIX).delete()
        Contact.objects.filter(client__client_code__startswith=DEMO_PREFIX).delete()
        Client.objects.filter(client_code__startswith=DEMO_PREFIX).delete()
        SalesForecast.objects.filter(model_version='demo-sales-v1').delete()
        self.stdout.write(self.style.WARNING('Existing DEMO Sales records removed.'))

    def _seed(self, owner, approver):
        today = timezone.localdate()
        now = timezone.now()

        client_specs = [
            ('DEMO-CL-001', 'ADNOC Gas Demonstration Account', 'oil_gas', 'platinum', 'Abu Dhabi', 92, 'low'),
            ('DEMO-CL-002', 'TAQA Transmission Demonstration Account', 'power_generation', 'gold', 'Abu Dhabi', 84, 'low'),
            ('DEMO-CL-003', 'EWEC Demonstration Account', 'water_treatment', 'gold', 'Abu Dhabi', 76, 'medium'),
            ('DEMO-CL-004', 'Masdar Demonstration Account', 'power_generation', 'silver', 'Abu Dhabi', 81, 'low'),
            ('DEMO-CL-005', 'Emirates Aluminium Demonstration Account', 'manufacturing', 'silver', 'Dubai', 64, 'medium'),
        ]
        clients = {}
        for code, name, industry, tier, city, health, churn in client_specs:
            client, _ = Client.objects.update_or_create(
                client_code=code,
                defaults={
                    'company_name': name,
                    'legal_name': name,
                    'trading_name': name.replace(' Demonstration Account', ''),
                    'industry_type': industry,
                    'client_tier': tier,
                    'city': city,
                    'country': 'United Arab Emirates',
                    'operating_locations': [city, 'UAE'],
                    'market_sectors': [industry],
                    'account_manager': owner,
                    'status': 'active',
                    'verification_status': 'verified',
                    'verified_by': approver,
                    'verified_at': now - timedelta(days=45),
                    'new_proposals_permitted': True,
                    'health_score': health,
                    'churn_risk': churn,
                    'last_contact_date': today - timedelta(days=7),
                    'lifetime_value': Decimal('8500000.00'),
                    'tags': ['demo', 'consulting-sales'],
                    'notes': 'Development demonstration record; not a production customer record.',
                },
            )
            clients[code] = client

        contact_names = [
            ('DEMO-CL-001', 'Aisha', 'Al Mansoori', 'Engineering Contracts Manager', 'procurement'),
            ('DEMO-CL-002', 'Omar', 'Al Nuaimi', 'Projects Director', 'decision_maker'),
            ('DEMO-CL-003', 'Mariam', 'Al Mazrouei', 'Technical Services Manager', 'technical'),
            ('DEMO-CL-004', 'Khalid', 'Al Falasi', 'Renewables Programme Lead', 'decision_maker'),
            ('DEMO-CL-005', 'Sara', 'Al Shamsi', 'Procurement Category Manager', 'procurement'),
        ]
        contacts = {}
        for client_code, first, last, title, role in contact_names:
            contact, _ = Contact.objects.update_or_create(
                client=clients[client_code],
                email=f'{first}.{last}.demo@example.test'.lower(),
                defaults={
                    'first_name': first,
                    'last_name': last,
                    'job_title': title,
                    'department': 'Engineering and Projects',
                    'role_type': role,
                    'is_primary': True,
                    'is_active': True,
                    'notes': 'Fictional contact for local demonstration only.',
                },
            )
            contacts[client_code] = contact

        framework_specs = [
            {
                'framework_number': 'DEMO-FW-001',
                'title': 'Multi-discipline engineering services framework',
                'client': clients['DEMO-CL-001'],
                'status': 'active',
                'effective_date': today - timedelta(days=240),
                'expiry_date': today + timedelta(days=490),
                'ceiling_value': Decimal('30000000'),
                'committed_value': Decimal('11750000'),
            },
            {
                'framework_number': 'DEMO-FW-002',
                'title': 'Power systems consultancy framework',
                'client': clients['DEMO-CL-002'],
                'status': 'expiring',
                'effective_date': today - timedelta(days=640),
                'expiry_date': today + timedelta(days=42),
                'ceiling_value': Decimal('18000000'),
                'committed_value': Decimal('15100000'),
            },
            {
                'framework_number': 'DEMO-FW-003',
                'title': 'Renewable energy advisory framework',
                'client': clients['DEMO-CL-004'],
                'status': 'pending_signature',
                'effective_date': today + timedelta(days=14),
                'expiry_date': today + timedelta(days=744),
                'ceiling_value': Decimal('12000000'),
                'committed_value': Decimal('0'),
            },
        ]
        frameworks = {}
        for spec in framework_specs:
            framework, _ = FrameworkAgreement.objects.update_or_create(
                framework_number=spec['framework_number'],
                defaults={
                    **spec,
                    'owner': owner,
                    'renewal_action_date': spec['expiry_date'] - timedelta(days=60),
                    'included_services': ['engineering_design', 'project_management', 'consulting'],
                    'disciplines': ['process', 'mechanical', 'piping', 'electrical', 'instrumentation'],
                    'geographic_coverage': ['United Arab Emirates'],
                    'currency': 'AED',
                    'invoiced_value': spec['committed_value'] * Decimal('0.62'),
                    'rate_cards': [{'version': '2026.1', 'effective_from': str(today - timedelta(days=240)), 'status': 'approved'}],
                    'rate_escalation_method': 'Annual UAE CPI review, capped at 4 percent.',
                    'call_off_procedure': 'Client work order followed by Rejlers acceptance and mobilisation notice.',
                    'payment_terms': '45 days from approved invoice',
                    'compliance_requirements': ['ISO 9001', 'Client HSE prequalification'],
                    'signed_document': 'demo/framework-agreement.pdf' if spec['status'] == 'active' else '',
                    'approved_by': approver if spec['status'] == 'active' else None,
                    'approved_at': now - timedelta(days=230) if spec['status'] == 'active' else None,
                },
            )
            frameworks[framework.framework_number] = framework

        opportunity_specs = [
            ('DEMO-OPP-001', 'Digital substation asset integrity study', 'DEMO-CL-002', 'lead', 1800000, 35, 21, 'medium', None, 'Confirm client budget and decision process'),
            ('DEMO-OPP-002', 'Water transmission surge analysis', 'DEMO-CL-003', 'qualified', 950000, 18, -1, 'high', None, 'Complete bid/no-bid review'),
            ('DEMO-OPP-003', 'Habshan brownfield laser scanning', 'DEMO-CL-001', 'qualified', 2400000, 28, 2, 'medium', 'DEMO-FW-001', 'Nominate proposal manager'),
            ('DEMO-OPP-004', 'Green hydrogen concept selection', 'DEMO-CL-004', 'proposal', 4200000, 45, 3, 'high', None, 'Complete engineering estimate'),
            ('DEMO-OPP-005', 'Aluminium plant power quality study', 'DEMO-CL-005', 'proposal', 1250000, 32, 5, 'medium', None, 'Complete internal technical review'),
            ('DEMO-OPP-006', 'Ruwais flare network optimisation', 'DEMO-CL-001', 'proposal', 3600000, 38, 7, 'medium', 'DEMO-FW-001', 'Submit approved proposal'),
            ('DEMO-OPP-007', 'Offshore compression FEED support', 'DEMO-CL-001', 'negotiation', 6800000, 20, -8, 'critical', 'DEMO-FW-001', 'Resolve liability cap deviation'),
            ('DEMO-OPP-008', 'Grid stability advisory programme', 'DEMO-CL-002', 'award_pending', 5100000, 9, -15, 'high', 'DEMO-FW-002', 'Obtain award approval'),
            ('DEMO-OPP-009', 'Wastewater reuse detailed engineering', 'DEMO-CL-003', 'awarded', 2750000, -5, -40, 'low', None, 'Complete project handover acceptance'),
            ('DEMO-OPP-010', 'Solar programme owner engineering', 'DEMO-CL-004', 'converted', 8900000, -42, -70, 'low', None, 'Delivery mobilisation complete'),
            ('DEMO-OPP-011', 'LNG terminal debottlenecking study', 'DEMO-CL-001', 'lost', 2100000, -55, -80, 'medium', None, 'Capture lessons learned'),
            ('DEMO-OPP-012', 'Smelter expansion PMC services', 'DEMO-CL-005', 'no_bid', 7400000, -18, -30, 'high', None, 'Archive no-bid decision'),
        ]
        deals = {}
        for index, (code, name, client_code, stage, value, close_days, due_days, risk, framework_code, next_action) in enumerate(opportunity_specs, start=1):
            closed = stage in {'awarded', 'converted', 'lost', 'no_bid'}
            bid_decision = 'pending' if code in {'DEMO-OPP-001', 'DEMO-OPP-002'} else ('no_bid' if stage == 'no_bid' else 'bid')
            award_status = 'pending' if stage == 'award_pending' else ('approved' if stage in {'awarded', 'converted'} else 'not_submitted')
            deal, _ = Deal.objects.update_or_create(
                deal_code=code,
                defaults={
                    'deal_name': name,
                    'client': clients[client_code],
                    'client_contact': contacts[client_code],
                    'framework': frameworks.get(framework_code),
                    'stage': stage,
                    'stage_entered_at': now - timedelta(days=(index * 3) % 29 + 2),
                    'priority': risk if risk in {'high', 'critical'} else 'medium',
                    'estimated_value': Decimal(value),
                    'actual_value': Decimal(value) if stage in {'awarded', 'converted'} else None,
                    'currency': 'AED',
                    'expected_close_date': today + timedelta(days=close_days),
                    'actual_close_date': today + timedelta(days=close_days) if closed else None,
                    'next_action_date': today + timedelta(days=max(-3, min(close_days, 12))),
                    'next_action': next_action,
                    'submission_due_date': today + timedelta(days=due_days),
                    'expected_start_date': today + timedelta(days=close_days + 30),
                    'owner': owner,
                    'service_categories': ['engineering_design', 'consulting'],
                    'disciplines': ['process', 'mechanical', 'piping', 'electrical', 'instrumentation'],
                    'estimated_hours': Decimal(value) / Decimal('650'),
                    'project_duration_months': 8 + index % 7,
                    'scope_type': 'detailed_engineering' if index % 2 else 'feed',
                    'location': 'United Arab Emirates',
                    'delivery_office': 'Abu Dhabi',
                    'opportunity_source': 'Client tender portal' if index % 2 else 'Framework call-off',
                    'client_reference': f'CLIENT-RFQ-DEMO-{index:03d}',
                    'qualification_data': {'strategic_fit': 'high', 'capacity_checked': True, 'duplicate_checked': True},
                    'risk_level': risk,
                    'bid_decision': bid_decision,
                    'bid_decision_reason': 'Approved pursuit based on strategic fit and delivery capacity.' if bid_decision == 'bid' else ('Resource constraints during requested delivery window.' if bid_decision == 'no_bid' else ''),
                    'bid_decided_by': approver if bid_decision != 'pending' else None,
                    'bid_decided_at': now - timedelta(days=20) if bid_decision != 'pending' else None,
                    'award_status': award_status,
                    'award_reference': f'DEMO-AWARD-{index:03d}' if award_status != 'not_submitted' else '',
                    'award_date': today + timedelta(days=close_days) if award_status == 'approved' else None,
                    'award_value': Decimal(value) if award_status in {'pending', 'approved'} else None,
                    'award_submitted_by': owner if award_status != 'not_submitted' else None,
                    'award_submitted_at': now - timedelta(days=7) if award_status != 'not_submitted' else None,
                    'award_approved_by': approver if award_status == 'approved' else None,
                    'award_approved_at': now - timedelta(days=5) if award_status == 'approved' else None,
                    'nominated_project_manager': owner if stage in {'award_pending', 'awarded', 'converted'} else None,
                    'handover_data': {'payment_terms': '45 days', 'reporting_cycle': 'monthly'} if award_status != 'not_submitted' else {},
                    'loss_reason': 'Client selected incumbent based on mobilisation timing.' if stage == 'lost' else '',
                    'description': f'Demonstration opportunity covering {name.lower()}.',
                    'tags': ['demo', 'sales-lifecycle'],
                    'ai_win_probability': None,
                    'ai_recommended_actions': [next_action],
                },
            )
            deal.team_members.set([owner])
            deals[code] = deal

        quote_specs = [
            ('DEMO-PROP-005', 'DEMO-OPP-005', 'draft', 1250000, 910000, 35),
            ('DEMO-PROP-006', 'DEMO-OPP-006', 'submitted', 3600000, 2650000, 42),
            ('DEMO-PROP-007', 'DEMO-OPP-007', 'negotiation', 6500000, 4700000, 25),
            ('DEMO-PROP-008', 'DEMO-OPP-008', 'won', 5000000, 3620000, 18),
            ('DEMO-PROP-009', 'DEMO-OPP-009', 'won', 2750000, 1980000, 60),
            ('DEMO-PROP-010', 'DEMO-OPP-010', 'won', 8900000, 6440000, 60),
            ('DEMO-PROP-011', 'DEMO-OPP-011', 'lost', 2050000, 1490000, 14),
        ]
        quotes = {}
        for number, deal_code, status, total, cost, valid_days in quote_specs:
            deal = deals[deal_code]
            approved = status in {'submitted', 'negotiation', 'won', 'lost'}
            quote, _ = Quote.objects.update_or_create(
                quote_number=number,
                defaults={
                    'deal': deal,
                    'client': deal.client,
                    'version': 1,
                    'status': status,
                    'subtotal': Decimal(total),
                    'tax_amount': Decimal('0'),
                    'discount_amount': Decimal('0'),
                    'total_amount': Decimal(total),
                    'estimated_cost': Decimal(cost),
                    'expected_margin_percent': ((Decimal(total) - Decimal(cost)) / Decimal(total) * 100).quantize(Decimal('0.01')),
                    'currency': 'AED',
                    'issue_date': today - timedelta(days=12),
                    'valid_until': today + timedelta(days=valid_days),
                    'sent_date': now - timedelta(days=8) if approved else None,
                    'response_date': now - timedelta(days=3) if status in {'won', 'lost'} else None,
                    'line_items': [{'discipline': 'Engineering services', 'amount': total}],
                    'scope': deal.description,
                    'deliverables': ['Design basis', 'Engineering calculations', 'Issued deliverable package'],
                    'assumptions': ['Client data supplied to agreed schedule'],
                    'exclusions': ['Construction execution'],
                    'disciplines': deal.disciplines,
                    'estimated_hours': {'engineering': int(Decimal(total) / Decimal('650'))},
                    'expenses': Decimal(total) * Decimal('0.025'),
                    'subcontractor_costs': Decimal(total) * Decimal('0.04'),
                    'payment_terms': '45 days from approved invoice',
                    'commercial_deviations': ['Liability capped at contract value'] if deal_code == 'DEMO-OPP-007' else [],
                    'risks': [{'risk': 'Client data availability', 'rating': 'medium'}],
                    'prepared_by': owner,
                    'approved_by': approver if approved else None,
                    'approved_at': now - timedelta(days=10) if approved else None,
                    'approval_history': [{'decision': 'approved', 'actor': approver.email}] if approved else [],
                    'submitted_version_hash': f'demo-{number.lower()}' if approved else '',
                    'submission_recipient': deal.client_contact.email if approved else '',
                    'submission_evidence': 'demo/submission-receipt.eml' if approved else '',
                    'notes': 'Development demonstration proposal.',
                },
            )
            quotes[deal_code] = quote

        project, _ = Project.objects.update_or_create(
            code='DEMO-SALES-PRJ-001',
            defaults={
                'name': deals['DEMO-OPP-010'].deal_name,
                'description': 'Project created from the completed demonstration Sales handover.',
                'status': 'planning',
                'priority': 'high',
                'progress': 0,
                'start_date': today + timedelta(days=14),
                'end_date': today + timedelta(days=420),
                'owner': owner,
                'budget': Decimal('6440000'),
                'spent': Decimal('0'),
                'contract_value': Decimal('8900000'),
                'currency': 'AED',
                'scope_type': 'feed',
                'client_name': clients['DEMO-CL-004'].company_name,
                'location': 'Abu Dhabi, UAE',
                'tags': ['demo', 'sales-handover'],
                'custom_fields': {'source_opportunity_code': 'DEMO-OPP-010'},
                'is_deleted': False,
            },
        )
        ProjectMember.objects.update_or_create(
            project=project,
            user=owner,
            defaults={'role': 'project_manager', 'is_active': True},
        )
        converted = deals['DEMO-OPP-010']
        converted.converted_project = project
        converted.converted_by = approver
        converted.converted_at = now - timedelta(days=2)
        converted.save(update_fields=['converted_project', 'converted_by', 'converted_at', 'updated_at'])

        handover_specs = [
            ('DEMO-OPP-009', 'acceptance_pending', None, None),
            ('DEMO-OPP-010', 'project_created', owner, project),
        ]
        for deal_code, status, accepted_by, linked_project in handover_specs:
            deal = deals[deal_code]
            ProjectHandover.objects.update_or_create(
                opportunity=deal,
                defaults={
                    'proposal': quotes[deal_code],
                    'status': status,
                    'owner': owner,
                    'project_manager': owner,
                    'signed_contract_reference': f'DEMO-CONTRACT-{deal_code[-3:]}',
                    'purchase_order_reference': f'DEMO-PO-{deal_code[-3:]}',
                    'contract_value': deal.award_value,
                    'currency': deal.currency,
                    'contract_start_date': today + timedelta(days=14),
                    'contract_end_date': today + timedelta(days=380),
                    'contract_differences': [{'field': 'start_date', 'proposal': str(today + timedelta(days=7)), 'contract': str(today + timedelta(days=14))}],
                    'billing_milestones': [
                        {'name': 'Mobilisation', 'percent': 10},
                        {'name': '60 percent design review', 'percent': 40},
                        {'name': 'Final delivery', 'percent': 50},
                    ],
                    'checklist': HANDOVER_CHECKLIST,
                    'delivery_data': {'office': 'Abu Dhabi', 'disciplines': deal.disciplines},
                    'meeting_at': now + timedelta(days=2) if status == 'acceptance_pending' else now - timedelta(days=4),
                    'acceptance_comment': 'Delivery inputs verified and accepted.' if accepted_by else '',
                    'accepted_by': accepted_by,
                    'accepted_at': now - timedelta(days=3) if accepted_by else None,
                    'project': linked_project,
                },
            )

        for deal in deals.values():
            OpportunityAuditEvent.objects.filter(opportunity=deal).delete()
            OpportunityAuditEvent.objects.create(
                opportunity=deal,
                event_type='demo_opportunity_created',
                from_stage='',
                to_stage='lead',
                reason='Demonstration lifecycle record created.',
                actor=owner,
            )
            if deal.stage != 'lead':
                OpportunityAuditEvent.objects.create(
                    opportunity=deal,
                    event_type='demo_current_stage',
                    from_stage='lead',
                    to_stage=deal.stage,
                    reason='Representative governed lifecycle history for demonstration.',
                    actor=approver,
                    data={'demo': True, 'bid_decision': deal.bid_decision, 'award_status': deal.award_status},
                )

        SalesActivity.objects.filter(deal__in=deals.values()).delete()
        activity_specs = [
            ('DEMO-OPP-002', 'meeting', 'Bid/no-bid review requested', 1, 'Management review scheduled.'),
            ('DEMO-OPP-004', 'follow_up', 'Engineering estimate coordination', 0, 'Discipline leads committed hours.'),
            ('DEMO-OPP-006', 'proposal', 'Proposal submitted to client portal', -2, 'Submission receipt recorded.'),
            ('DEMO-OPP-007', 'negotiation', 'Commercial clarification meeting', -1, 'Liability cap remains open.'),
            ('DEMO-OPP-009', 'meeting', 'Sales-to-project handover meeting', 2, 'Awaiting Project Manager acceptance.'),
            ('DEMO-OPP-010', 'meeting', 'Project mobilisation handover completed', -4, 'Delivery team accepted the assignment.'),
        ]
        for deal_code, activity_type, subject, follow_up_days, outcome in activity_specs:
            deal = deals[deal_code]
            SalesActivity.objects.create(
                client=deal.client,
                deal=deal,
                contact=deal.client_contact,
                activity_type=activity_type,
                subject=subject,
                description=f'Demonstration activity for {deal.deal_name}.',
                activity_date=now - timedelta(days=abs(follow_up_days) + 1),
                duration_minutes=45,
                performed_by=owner,
                outcome=outcome,
                next_steps=deal.next_action,
                follow_up_date=today + timedelta(days=follow_up_days),
                sentiment_score=0.65,
                key_topics=['scope', 'commercial', 'schedule'],
            )

        quarter = ((today.month - 1) // 3) + 1
        forecast_specs = [
            (f'{today.year}-Q{quarter}', today, 'approved', 10225000, 16200000, 7450000, None),
            (today.strftime('%Y-%m'), today + timedelta(days=1), 'management_review', 3850000, 5200000, 2600000, None),
            (f'{today.year}-Q{max(1, quarter - 1)}', today - timedelta(days=90), 'superseded', 9100000, 13800000, 6800000, 8650000),
        ]
        for period, forecast_date, status, predicted, best, worst, actual in forecast_specs:
            SalesForecast.objects.update_or_create(
                forecast_period=period,
                forecast_date=forecast_date,
                defaults={
                    'status': status,
                    'predicted_revenue': Decimal(predicted),
                    'confidence_level': 0.82,
                    'best_case': Decimal(best),
                    'worst_case': Decimal(worst),
                    'actual_revenue': Decimal(actual) if actual is not None else None,
                    'accuracy': 0.95 if actual is not None else None,
                    'model_version': 'demo-sales-v1',
                    'training_data_points': len(deals),
                    'features_used': ['stage', 'probability', 'award_date', 'capacity'],
                    'forecast_by_stage': {'qualified': 837500, 'proposal': 2425000, 'negotiation': 5100000, 'award_pending': 4590000},
                    'forecast_by_service': {'engineering_design': 7200000, 'consulting': 3025000},
                    'top_deals_considered': ['DEMO-OPP-007', 'DEMO-OPP-008', 'DEMO-OPP-004'],
                    'category_totals': {'pipeline': 26100000, 'weighted_pipeline': 13542500, 'commit': 5100000, 'best_case': best},
                    'demand_by_discipline': {'process': 4200, 'mechanical': 3100, 'piping': 4800, 'electrical': 2600, 'instrumentation': 2900},
                    'manual_adjustments': [{'amount': 250000, 'reason': 'Expected award timing moved into current quarter', 'owner': owner.email}],
                    'source_snapshot': {'opportunities': len(deals), 'generated_for_demo': True},
                    'exchange_rate_date': today,
                    'exchange_rate_source': 'Demo base currency AED; no conversion applied',
                    'generated_by': owner,
                    'approved_by': approver if status == 'approved' else None,
                    'approved_at': now if status == 'approved' else None,
                },
            )

        return {
            'clients': len(clients),
            'contacts': len(contacts),
            'frameworks': len(frameworks),
            'opportunities': len(deals),
            'proposals': len(quotes),
            'handovers': len(handover_specs),
            'projects': 1,
            'forecasts': len(forecast_specs),
            'activities': len(activity_specs),
        }
