"""
Management command to seed initial approval routes
Based on actual approval workflow:
- Project Inv → Finance Team → Richa (Procurement) → Project Manager (cc Jamal & Rafat) → Mo (VP) → Jarmo (CEO)
- Gen/Admin Inv → Finance Team → Richa → HR/Admin → Jarmo (CEO)
- Acc/Fin Inv → Finance Team → Richa → Aneef → Aleksi (CFO) → Jarmo (CEO)
- IT Inv → Finance Team → Richa → Sherwin/Nijum (ICT) → Aleksi (CFO) → Jarmo (CEO)
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.finance.models import ApprovalRoute, InvoiceType


class Command(BaseCommand):
    help = 'Seed initial approval routes for finance module with actual team members'
    
    def handle(self, *args, **options):
        self.stdout.write('Creating approval routes with actual team members...')
        
        # Get email addresses from settings
        richa_email = getattr(settings, 'RICHA_EMAIL', 'richa@company.com')
        jamal_email = getattr(settings, 'JAMAL_EMAIL', 'jamal@company.com')
        rafat_email = getattr(settings, 'RAFAT_EMAIL', 'rafat@company.com')
        moe_email = getattr(settings, 'MOE_EMAIL', 'mo@company.com')
        jarmo_email = getattr(settings, 'JARMO_EMAIL', 'jarmo@company.com')
        aneef_email = getattr(settings, 'ANEEF_EMAIL', 'aneef@company.com')
        aleksi_email = getattr(settings, 'ALEKSI_EMAIL', 'aleksi@company.com')
        sherwin_email = getattr(settings, 'SHERWIN_EMAIL', 'sherwin@company.com')
        nijum_email = getattr(settings, 'NIJUM_EMAIL', 'nijum@company.com')
        hr_admin_email = getattr(settings, 'HR_ADMIN_EMAIL', 'hradmin@company.com')
        
        # Acc/Fin Inv → Finance Team → Richa → Aneef → Aleksi (CFO) → Jarmo (CEO)
        finance_route, created = ApprovalRoute.objects.update_or_create(
            invoice_type=InvoiceType.FINANCE,
            defaults={
                'approval_chain': [
                    {
                        "level": 1,
                        "name": "Richa",
                        "email": richa_email,
                        "title": "Procurement",
                        "mandatory": True
                    },
                    {
                        "level": 2,
                        "name": "Aneef",
                        "email": aneef_email,
                        "title": "Finance Manager",
                        "mandatory": True
                    },
                    {
                        "level": 3,
                        "name": "Aleksi",
                        "email": aleksi_email,
                        "title": "CFO",
                        "mandatory": True
                    },
                    {
                        "level": 4,
                        "name": "Jarmo",
                        "email": jarmo_email,
                        "title": "CEO",
                        "mandatory": True
                    }
                ],
                'is_active': True,
                'priority': 1
            }
        )
        
        self.stdout.write(self.style.SUCCESS(f'✓ {"Created" if created else "Updated"} Finance/Accounting approval route'))
        self.stdout.write(f'  Chain: Richa → Aneef → Aleksi (CFO) → Jarmo (CEO)')
        
        # IT Inv → Finance Team → Richa → Sherwin/Nijum (ICT) → Aleksi (CFO) → Jarmo (CEO)
        it_route, created = ApprovalRoute.objects.update_or_create(
            invoice_type=InvoiceType.IT,
            defaults={
                'approval_chain': [
                    {
                        "level": 1,
                        "name": "Richa",
                        "email": richa_email,
                        "title": "Procurement",
                        "mandatory": True
                    },
                    {
                        "level": 2,
                        "name": "Sherwin/Nijum",
                        "email": sherwin_email,
                        "title": "ICT Manager",
                        "cc": [nijum_email],
                        "mandatory": True
                    },
                    {
                        "level": 3,
                        "name": "Aleksi",
                        "email": aleksi_email,
                        "title": "CFO",
                        "mandatory": True
                    },
                    {
                        "level": 4,
                        "name": "Jarmo",
                        "email": jarmo_email,
                        "title": "CEO",
                        "mandatory": True
                    }
                ],
                'is_active': True,
                'priority': 1
            }
        )
        
        self.stdout.write(self.style.SUCCESS(f'✓ {"Created" if created else "Updated"} IT approval route'))
        self.stdout.write(f'  Chain: Richa → Sherwin (cc Nijum) → Aleksi (CFO) → Jarmo (CEO)')
        
        # Project Inv → Finance Team → Richa (Procurement) → Project Manager (cc Jamal & Rafat) → Mo (VP) → Jarmo (CEO)
        project_route, created = ApprovalRoute.objects.update_or_create(
            invoice_type=InvoiceType.PROJECT,
            defaults={
                'approval_chain': [
                    {
                        "level": 1,
                        "name": "Richa",
                        "email": richa_email,
                        "title": "Procurement",
                        "mandatory": True
                    },
                    {
                        "level": 2,
                        "name": "Project Manager",
                        "email": jamal_email,  # Primary PM
                        "title": "Project Manager",
                        "cc": [rafat_email],  # CC Rafat
                        "mandatory": True
                    },
                    {
                        "level": 3,
                        "name": "Mo",
                        "email": moe_email,
                        "title": "Vice President",
                        "mandatory": True
                    },
                    {
                        "level": 4,
                        "name": "Jarmo",
                        "email": jarmo_email,
                        "title": "CEO",
                        "mandatory": True
                    }
                ],
                'is_active': True,
                'priority': 1
            }
        )
        
        self.stdout.write(self.style.SUCCESS(f'✓ {"Created" if created else "Updated"} Project approval route'))
        self.stdout.write(f'  Chain: Richa → Project Manager (cc Jamal & Rafat) → Mo (VP) → Jarmo (CEO)')
        
        # Gen/Admin Inv → Finance Team → Richa → HR/Admin → Jarmo (CEO)
        admin_route, created = ApprovalRoute.objects.update_or_create(
            invoice_type=InvoiceType.ADMIN,
            defaults={
                'approval_chain': [
                    {
                        "level": 1,
                        "name": "Richa",
                        "email": richa_email,
                        "title": "Procurement",
                        "mandatory": True
                    },
                    {
                        "level": 2,
                        "name": "HR/Admin",
                        "email": hr_admin_email,
                        "title": "HR/Admin Manager",
                        "mandatory": True
                    },
                    {
                        "level": 3,
                        "name": "Jarmo",
                        "email": jarmo_email,
                        "title": "CEO",
                        "mandatory": True
                    }
                ],
                'is_active': True,
                'priority': 1
            }
        )
        
        self.stdout.write(self.style.SUCCESS(f'✓ {"Created" if created else "Updated"} Admin/General approval route'))
        self.stdout.write(f'  Chain: Richa → HR/Admin → Jarmo (CEO)')
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('✅ Approval routes setup complete!'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write('')
        self.stdout.write('Summary of Approval Chains:')
        self.stdout.write('')
        self.stdout.write('  1. Finance/Accounting Invoices (4 levels):')
        self.stdout.write(f'     Richa ({richa_email}) → Aneef ({aneef_email})')
        self.stdout.write(f'     → Aleksi/CFO ({aleksi_email}) → Jarmo/CEO ({jarmo_email})')
        self.stdout.write('')
        self.stdout.write('  2. IT Invoices (4 levels):')
        self.stdout.write(f'     Richa ({richa_email}) → Sherwin ({sherwin_email}, cc: {nijum_email})')
        self.stdout.write(f'     → Aleksi/CFO ({aleksi_email}) → Jarmo/CEO ({jarmo_email})')
        self.stdout.write('')
        self.stdout.write('  3. Project Invoices (4 levels):')
        self.stdout.write(f'     Richa ({richa_email}) → PM/Jamal ({jamal_email}, cc: {rafat_email})')
        self.stdout.write(f'     → Mo/VP ({moe_email}) → Jarmo/CEO ({jarmo_email})')
        self.stdout.write('')
        self.stdout.write('  4. Admin/General Invoices (3 levels):')
        self.stdout.write(f'     Richa ({richa_email}) → HR/Admin ({hr_admin_email})')
        self.stdout.write(f'     → Jarmo/CEO ({jarmo_email})')
        self.stdout.write('')
        self.stdout.write('View/Edit routes in Django Admin:')
        self.stdout.write('http://localhost:8000/admin/finance/approvalroute/')
        self.stdout.write('')
