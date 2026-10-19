"""Run via manage.py shell after fresh migrations on the disposable database.

Uses the full application URL table and model registry; every synthetic journey
is rolled back. Refuses all application databases and ordinary PostgreSQL hosts.
"""
from django.conf import settings
from django.db import connection, transaction
from django.test import override_settings

from apps.sales.tests.test_opportunity_proposal_flow import OpportunityProposalFlowTests


def verify():
    database = settings.DATABASES['default']
    if (database.get('NAME') != 'radai_sales_verification'
            or database.get('HOST') != 'radai_sales_verify_20261019'
            or connection.vendor != 'postgresql'):
        raise RuntimeError('This verification requires the explicitly provisioned disposable Sales database.')
    with override_settings(
        ROOT_URLCONF='config.urls', RADAI_BUSINESS_APPROVAL_ROUTES={},
        SALES_BID_DECISION_RBAC_FALLBACK_ENABLED=True, SALES_WORKSPACE_ENABLED=False,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                            'LOCATION': 'migrated-sales-flow'}},
    ):
        for decision in ('bid', 'conditional_bid'):
            with transaction.atomic():
                case = OpportunityProposalFlowTests('test_bid_to_saved_draft')
                try:
                    case.setUp()
                    case.journey(decision)
                    print(f'MIGRATED_SALES_FLOW_PASS: {decision}, full application routes, real PostgreSQL')
                finally:
                    case.doCleanups()
                    transaction.set_rollback(True)


verify()
