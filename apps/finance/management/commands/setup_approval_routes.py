"""
Django Management Command: Setup Finance Approval Routes
Creates predefined approval routes for different invoice types
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.finance.models import ApprovalRoute, InvoiceType


class Command(BaseCommand):
    help = 'Setup predefined approval routes for finance invoices'

    def handle(self, *args, **options):
        self.stdout.write("Setting up Finance Approval Routes...")
        
        # Delete existing approval routes
        ApprovalRoute.objects.all().delete()
        self.stdout.write(self.style.SUCCESS("✓ Deleted existing routes"))
        
        routes_created = 0
        
        # Define approval chains for each invoice type
        approval_chains = {
            InvoiceType.PROJECT: [
                {
                    "level": 1,
                    "name": "Richa (Procurement)",
                    "email": settings.RICHA_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "Procurement Approval"
                },
                {
                    "level": 2,
                    "name": "Project Manager",
                    "email": "pm@company.com",
                    "cc_emails": [settings.JAMAL_EMAIL, settings.RAFAT_EMAIL],
                    "is_mandatory": True,
                    "title": "Project Manager Approval"
                },
                {
                    "level": 3,
                    "name": "Mo (VP)",
                    "email": settings.MOE_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "VP Approval"
                },
                {
                    "level": 4,
                    "name": "Jarmo (CEO)",
                    "email": settings.JARMO_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "CEO Final Approval"
                }
            ],
            InvoiceType.ADMIN: [
                {
                    "level": 1,
                    "name": "Richa (Procurement)",
                    "email": settings.RICHA_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "Procurement Approval"
                },
                {
                    "level": 2,
                    "name": "HR/Admin",
                    "email": settings.HR_ADMIN_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "HR/Admin Approval"
                },
                {
                    "level": 3,
                    "name": "Jarmo (CEO)",
                    "email": settings.JARMO_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "CEO Final Approval"
                }
            ],
            InvoiceType.FINANCE: [
                {
                    "level": 1,
                    "name": "Richa (Procurement)",
                    "email": settings.RICHA_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "Procurement Approval"
                },
                {
                    "level": 2,
                    "name": "Aneef (Finance)",
                    "email": settings.ANEEF_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "Finance Approval"
                },
                {
                    "level": 3,
                    "name": "Aleksi (CFO)",
                    "email": settings.ALEKSI_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "CFO Approval"
                },
                {
                    "level": 4,
                    "name": "Jarmo (CEO)",
                    "email": settings.JARMO_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "CEO Final Approval"
                }
            ],
            InvoiceType.IT: [
                {
                    "level": 1,
                    "name": "Richa (Procurement)",
                    "email": settings.RICHA_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "Procurement Approval"
                },
                {
                    "level": 2,
                    "name": "Sherwin (ICT)",
                    "email": settings.SHERWIN_EMAIL,
                    "cc_emails": [settings.NIJUM_EMAIL],
                    "is_mandatory": True,
                    "title": "ICT Approval"
                },
                {
                    "level": 3,
                    "name": "Aleksi (CFO)",
                    "email": settings.ALEKSI_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "CFO Approval"
                },
                {
                    "level": 4,
                    "name": "Jarmo (CEO)",
                    "email": settings.JARMO_EMAIL,
                    "cc_emails": [],
                    "is_mandatory": True,
                    "title": "CEO Final Approval"
                }
            ]
        }
        
        # Create approval routes
        for invoice_type, chain in approval_chains.items():
            route = ApprovalRoute.objects.create(
                invoice_type=invoice_type,
                approval_chain=chain,
                is_active=True,
                priority=100
            )
            routes_created += 1
            self.stdout.write(self.style.SUCCESS(
                f"✓ Created {route.get_invoice_type_display()} route with {len(chain)} levels"
            ))
        
        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Successfully created {routes_created} approval routes!"
        ))
