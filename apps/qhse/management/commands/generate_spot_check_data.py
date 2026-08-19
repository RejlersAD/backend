"""
Management Command: Generate Sample QHSE Spot Check Data
Intelligently creates realistic demo data using soft-coding patterns
Usage: python manage.py generate_spot_check_data [--count=50]
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.qhse.models import QHSESpotCheckRegister, QHSERunningProject
from datetime import timedelta, datetime, time
import random

User = get_user_model()


class Command(BaseCommand):
    help = 'Generate sample QHSE spot check data for testing and demonstration'
    
    # Soft-coded configuration for realistic data generation
    CONFIG = {
        'engineers': [
            'Mohammed Al-Rashid',
            'Ahmed Hassan',
            'Fatima Al-Mansouri',
            'Omar Abdullah',
            'Sara Al-Mazrouei',
            'Khalid Rahman',
            'Aisha Mohammed',
            'Ali Al-Hashimi'
        ],
        'clients': [
            'ADNOC',
            'TAQA',
            'Emirates Nuclear Energy Corporation',
            'Dubai Electricity & Water Authority',
            'Abu Dhabi Municipality',
            'Dubai Roads & Transport Authority',
            'Sharjah Electricity & Water Authority',
            'Etihad Rail'
        ],
        'categories': [
            ('COMPLIANT', 'Compliant', 0.45),  # 45% compliant
            ('OBSERVATION', 'Observation', 0.25),  # 25% observations
            ('MINOR', 'Minor Issue', 0.15),  # 15% minor
            ('CAR', 'Corrective Action Request', 0.10),  # 10% CAR
            ('MAJOR', 'Major Issue', 0.04),  # 4% major
            ('NCR', 'Non-Conformance Report', 0.01),  # 1% NCR
        ],
        'document_types': [
            'Project Quality Plan',
            'Inspection Test Plan',
            'Method Statement',
            'Risk Assessment',
            'Safety Data Sheet',
            'Environmental Impact Assessment',
            'Quality Control Procedure',
            'Work Permit',
            'Material Approval',
            'Calibration Certificate'
        ],
        'comments_templates': {
            'COMPLIANT': [
                'Document reviewed and found compliant with project requirements.',
                'All required sections completed accurately.',
                'Proper approvals and signatures verified.',
                'Documentation meets quality standards.',
                'Format and content conform to specifications.'
            ],
            'OBSERVATION': [
                'Minor formatting issues noted.',
                'Recommend updating references to latest standards.',
                'Consider adding more detail in section {section}.',
                'Suggested improvement: clarify {item}.',
                'Advisory: update document revision history.'
            ],
            'MINOR': [
                'Missing signature in approval section.',
                'Date format inconsistent throughout document.',
                'Reference document number incorrect.',
                'Incomplete checklist in appendix.',
                'Minor calculation error in section {section}.'
            ],
            'CAR': [
                'Critical safety requirement not addressed.',
                'Missing mandatory quality control checkpoint.',
                'Non-compliance with project specification detected.',
                'Required inspection not documented.',
                'Essential equipment calibration certificate missing.'
            ],
            'MAJOR': [
                'Serious deviation from approved procedure identified.',
                'Major safety concern requires immediate attention.',
                'Critical non-conformance with regulatory requirements.',
                'Significant quality issue affecting deliverable.',
                'Major design discrepancy requires engineering review.'
            ],
            'NCR': [
                'Non-conformance: {item} does not meet specification requirements.',
                'Material quality issue - reject and replace required.',
                'Workmanship below acceptable standard - rework needed.',
                'Critical dimension out of tolerance.',
                'Non-conforming product - quarantine and investigate.'
            ]
        },
        'document_titles_by_type': {
            'Project Quality Plan': [
                'Overall Project Quality Plan - Rev {rev}',
                'Phase {phase} Quality Plan',
                'Quality Assurance Plan - {project_type}',
                'Integrated Quality Management Plan'
            ],
            'Inspection Test Plan': [
                '{system} System ITP - Rev {rev}',
                'Structural Steel ITP',
                'Mechanical Equipment ITP',
                'Electrical Installation ITP',
                'Piping System ITP - Phase {phase}'
            ],
            'Method Statement': [
                '{activity} Method Statement',
                'Safe Work Method Statement - {work}',
                'Construction Methodology - {area}',
                'Installation Procedure - {equipment}'
            ],
            'Risk Assessment': [
                'Job Safety Analysis - {activity}',
                'Risk Assessment Matrix - {area}',
                'HAZOP Study Report - {system}',
                'Environmental Risk Assessment'
            ]
        },
        'originators': [
            'Engineering Team',
            'Project Manager',
            'Lead Engineer',
            'Design Consultant',
            'Construction Manager',
            'QA/QC Manager',
            'Safety Officer',
            'Technical Lead'
        ],
        'status_distribution': {
            'COMPLIANT': 'CLOSED',
            'OBSERVATION': ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'],
            'MINOR': ['OPEN', 'IN_PROGRESS', 'RESOLVED'],
            'CAR': ['OPEN', 'IN_PROGRESS'],
            'MAJOR': ['OPEN', 'IN_PROGRESS'],
            'NCR': ['OPEN', 'IN_PROGRESS']
        }
    }
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=50,
            help='Number of spot check records to generate (default: 50)'
        )
        parser.add_argument(
            '--days-back',
            type=int,
            default=180,
            help='Generate data going back this many days (default: 180)'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing spot check data before generating'
        )
    
    def handle(self, *args, **options):
        count = options['count']
        days_back = options['days_back']
        clear_existing = options['clear']
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*70))
        self.stdout.write(self.style.SUCCESS('📊 QHSE Spot Check Data Generator'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))
        
        # Clear existing data if requested
        if clear_existing:
            existing_count = QHSESpotCheckRegister.objects.count()
            QHSESpotCheckRegister.objects.all().delete()
            self.stdout.write(self.style.WARNING(
                f'🗑️  Cleared {existing_count} existing spot check records\n'
            ))
        
        # Get existing projects or create sample ones
        projects = self.get_or_create_projects()
        
        # Get admin user for created_by field
        admin_user = User.objects.filter(is_superuser=True).first()
        
        # Generate spot check records
        self.stdout.write('🔄 Generating spot check records...\n')
        created_records = []
        start_sr_no = QHSESpotCheckRegister.objects.count() + 1
        
        for i in range(count):
            project = random.choice(projects)
            category, category_label, _ = self.weighted_random_category()
            
            # Generate date (distributed across time period)
            days_ago = random.randint(0, days_back)
            check_date = (timezone.now() - timedelta(days=days_ago)).date()
            check_time = time(hour=random.randint(8, 16), minute=random.choice([0, 15, 30, 45]))
            
            # Generate document details
            doc_type = random.choice(self.CONFIG['document_types'])
            doc_title = self.generate_document_title(doc_type)
            doc_number = self.generate_document_number(project.project_no, doc_type, i)
            
            # Generate comments based on category
            comments = self.generate_comments(category)
            
            # Determine status
            status = self.determine_status(category, days_ago)
            
            # Calculate resolution date if applicable
            resolution_date = None
            resolution_comments = None
            if status in ['RESOLVED', 'CLOSED']:
                resolution_days = random.randint(5, 30)
                resolution_date = check_date + timedelta(days=resolution_days)
                resolution_comments = self.generate_resolution_comments(category)
            
            # Create spot check record
            spot_check = QHSESpotCheckRegister.objects.create(
                sr_no=start_sr_no + i,
                project_no=project.project_no,
                project_title=project.project_title,
                client=project.client or random.choice(self.CONFIG['clients']),
                qhse_engineer=random.choice(self.CONFIG['engineers']),
                date_of_spot_check=check_date,
                time=check_time,
                document_no=doc_number,
                document_title=doc_title,
                originator_lead=random.choice(self.CONFIG['originators']),
                comments=comments,
                category=category,
                remarks=self.generate_remarks(category, doc_type),
                status=status,
                resolution_date=resolution_date,
                resolution_comments=resolution_comments,
                created_by=admin_user,
                updated_by=admin_user,
                is_active=True
            )
            created_records.append(spot_check)
            
            # Progress indicator
            if (i + 1) % 10 == 0:
                self.stdout.write(f'  ✓ Created {i + 1}/{count} records...')
        
        # Summary statistics
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS('✅ Generation Complete!\n'))
        self.stdout.write(f'Total Records Created: {len(created_records)}')
        self.stdout.write(f'Date Range: {min(r.date_of_spot_check for r in created_records)} to {max(r.date_of_spot_check for r in created_records)}')
        self.stdout.write(f'Projects Covered: {len(set(r.project_no for r in created_records))}')
        
        # Category breakdown
        self.stdout.write('\n📊 Category Distribution:')
        for category, _, _ in self.CONFIG['categories']:
            cat_count = len([r for r in created_records if r.category == category])
            percentage = (cat_count / len(created_records)) * 100
            self.stdout.write(f'  • {category}: {cat_count} ({percentage:.1f}%)')
        
        # Status breakdown
        self.stdout.write('\n📋 Status Distribution:')
        statuses = {}
        for record in created_records:
            statuses[record.status] = statuses.get(record.status, 0) + 1
        for status, count in statuses.items():
            percentage = (count / len(created_records)) * 100
            self.stdout.write(f'  • {status}: {count} ({percentage:.1f}%)')
        
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS('🎉 Data generation successful!\n'))
    
    def get_or_create_projects(self):
        """Get existing projects or create sample ones"""
        projects = list(QHSERunningProject.objects.filter(is_active=True)[:10])
        
        if len(projects) < 3:
            self.stdout.write(self.style.WARNING('⚠️  Few projects found, creating sample projects...\n'))
            sample_projects = [
                {
                    'project_no': 'PRJ-2024-001',
                    'project_title': 'Abu Dhabi Metro Extension Phase 2',
                    'client': 'ADNOC'
                },
                {
                    'project_no': 'PRJ-2024-002',
                    'project_title': 'Dubai Smart City Infrastructure',
                    'client': 'Dubai Municipality'
                },
                {
                    'project_no': 'PRJ-2024-003',
                    'project_title': 'Renewable Energy Plant - Masdar',
                    'client': 'TAQA'
                },
                {
                    'project_no': 'PRJ-2024-004',
                    'project_title': 'Water Treatment Facility Upgrade',
                    'client': 'DEWA'
                },
                {
                    'project_no': 'PRJ-2024-005',
                    'project_title': 'High-Speed Rail Network Phase 1',
                    'client': 'Etihad Rail'
                }
            ]
            
            admin_user = User.objects.filter(is_superuser=True).first()
            for proj_data in sample_projects:
                if not QHSERunningProject.objects.filter(project_no=proj_data['project_no']).exists():
                    project = QHSERunningProject.objects.create(
                        sr_no=QHSERunningProject.objects.count() + 1,
                        project_no=proj_data['project_no'],
                        project_title=proj_data['project_title'],
                        client=proj_data['client'],
                        project_manager='Project Manager',
                        project_quality_eng='Quality Engineer',
                        created_by=admin_user,
                        is_active=True
                    )
                    projects.append(project)
        
        return projects
    
    def weighted_random_category(self):
        """Select category based on realistic distribution"""
        rand = random.random()
        cumulative = 0
        for category, label, weight in self.CONFIG['categories']:
            cumulative += weight
            if rand <= cumulative:
                return category, label, weight
        return self.CONFIG['categories'][0]  # Fallback
    
    def generate_document_number(self, project_no, doc_type, index):
        """Generate realistic document number"""
        type_codes = {
            'Project Quality Plan': 'PQP',
            'Inspection Test Plan': 'ITP',
            'Method Statement': 'MS',
            'Risk Assessment': 'RA',
            'Safety Data Sheet': 'SDS',
            'Environmental Impact Assessment': 'EIA',
            'Quality Control Procedure': 'QCP',
            'Work Permit': 'WP',
            'Material Approval': 'MAT',
            'Calibration Certificate': 'CAL'
        }
        code = type_codes.get(doc_type, 'DOC')
        rev = random.choice(['A', 'B', 'C', 'D', '0', '1', '2'])
        return f"{project_no}-{code}-{str(index + 1).zfill(4)}-Rev{rev}"
    
    def generate_document_title(self, doc_type):
        """Generate contextual document title"""
        templates = self.CONFIG['document_titles_by_type'].get(doc_type, [f'{doc_type} - {{project_type}}'])
        template = random.choice(templates)
        
        replacements = {
            '{rev}': random.choice(['A', 'B', 'C', '0', '1', '2']),
            '{phase}': random.choice(['1', '2', '3', 'A', 'B']),
            '{project_type}': random.choice(['Civil Works', 'MEP', 'Structural', 'Infrastructure']),
            '{system}': random.choice(['HVAC', 'Electrical', 'Plumbing', 'Fire Protection']),
            '{activity}': random.choice(['Excavation', 'Concrete Pouring', 'Steel Erection', 'Welding']),
            '{work}': random.choice(['Hot Work', 'Confined Space', 'Working at Height', 'Heavy Lifting']),
            '{area}': random.choice(['Zone A', 'Zone B', 'Building 1', 'Basement', 'Roof Level']),
            '{equipment}': random.choice(['Pumps', 'Generators', 'Transformers', 'Chillers'])
        }
        
        for key, value in replacements.items():
            template = template.replace(key, value)
        
        return template
    
    def generate_comments(self, category):
        """Generate contextual comments"""
        templates = self.CONFIG['comments_templates'].get(category, ['Spot check completed.'])
        template = random.choice(templates)
        
        replacements = {
            '{section}': random.choice(['3.2', '4.1', '5.3', 'Appendix A']),
            '{item}': random.choice(['acceptance criteria', 'responsibility matrix', 'testing procedures', 'reporting requirements'])
        }
        
        for key, value in replacements.items():
            template = template.replace(key, value)
        
        return template
    
    def determine_status(self, category, days_ago):
        """Determine realistic status based on category and age"""
        status_options = self.CONFIG['status_distribution'].get(category, ['OPEN'])
        
        if isinstance(status_options, str):
            return status_options
        
        # Ensure we have the right number of weights for options
        num_options = len(status_options)
        
        # Older items more likely to be closed
        if days_ago > 60:
            if num_options == 4:
                weights = [0.1, 0.2, 0.3, 0.4]
            elif num_options == 3:
                weights = [0.2, 0.3, 0.5]
            else:
                weights = [1.0 / num_options] * num_options
        elif days_ago > 30:
            if num_options == 4:
                weights = [0.2, 0.3, 0.3, 0.2]
            elif num_options == 3:
                weights = [0.3, 0.4, 0.3]
            else:
                weights = [1.0 / num_options] * num_options
        else:
            if num_options == 4:
                weights = [0.5, 0.3, 0.15, 0.05]
            elif num_options == 3:
                weights = [0.5, 0.3, 0.2]
            else:
                weights = [1.0 / num_options] * num_options
        
        return random.choices(status_options, weights=weights)[0]
    
    def generate_resolution_comments(self, category):
        """Generate resolution comments"""
        templates = {
            'COMPLIANT': 'Closed as compliant.',
            'OBSERVATION': random.choice([
                'Observation acknowledged and document updated.',
                'Recommendation implemented in revision.',
                'Advisory note added to project records.'
            ]),
            'MINOR': random.choice([
                'Issue corrected and document resubmitted.',
                'Minor deficiency addressed.',
                'Corrective action completed and verified.'
            ]),
            'CAR': random.choice([
                'Root cause analysis completed. Corrective actions implemented.',
                'Non-compliance addressed. Verification inspection passed.',
                'CAR closed after successful re-inspection.'
            ]),
            'MAJOR': random.choice([
                'Major issue resolved through engineering change.',
                'Comprehensive corrective action plan implemented and verified.',
                'Critical concern addressed with full documentation.'
            ]),
            'NCR': random.choice([
                'Non-conforming item replaced. Quality records updated.',
                'Rework completed and re-inspected successfully.',
                'Disposition: Rework completed to specification.'
            ])
        }
        return templates.get(category, 'Resolved.')
    
    def generate_remarks(self, category, doc_type):
        """Generate additional remarks"""
        if category in ['CAR', 'MAJOR', 'NCR']:
            return f'Priority action required. {doc_type} requires immediate attention and follow-up.'
        elif category == 'MINOR':
            return f'Minor corrections needed. {doc_type} to be resubmitted after updates.'
        elif category == 'OBSERVATION':
            return f'Informational observation. No immediate action required for {doc_type}.'
        else:
            return f'{doc_type} reviewed and accepted.'
