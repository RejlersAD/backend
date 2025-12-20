"""
Create sample CRS data for testing
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.crs.models import CRSDocument, CRSComment, CRSActivity, GoogleSheetConfig
from django.utils import timezone
import random

User = get_user_model()


class Command(BaseCommand):
    help = 'Create sample CRS documents and comments for testing'

    def handle(self, *args, **options):
        self.stdout.write('Creating sample CRS data...')
        
        # Get or create a user
        user = User.objects.first()
        if not user:
            self.stdout.write(self.style.ERROR('No users found. Please create a user first.'))
            return
        
        # Sample projects
        projects = [
            {
                'name': 'Oil Refinery Expansion - Phase 2',
                'contractor': 'ABC Engineering Ltd.',
                'doc_name': 'Piping Design Basis Review Comments',
                'doc_number': 'CRS-ORE-P2-001',
            },
            {
                'name': 'Gas Processing Plant',
                'contractor': 'XYZ Construction Corp.',
                'doc_name': 'Instrumentation Specifications Comments',
                'doc_number': 'CRS-GPP-IS-002',
            },
            {
                'name': 'Water Treatment Facility',
                'contractor': 'DEF Contractors',
                'doc_name': 'Electrical Design Review',
                'doc_number': 'CRS-WTF-ED-003',
            },
        ]
        
        # Sample comments
        sample_comments = [
            "Valve specification shall be updated to include material grade as per ASME B16.34",
            "Provide additional details on pressure relief valve sizing calculations",
            "Clarify piping material selection criteria for corrosive service",
            "Update P&ID to reflect latest instrument list revisions",
            "Include seismic design considerations for critical equipment",
            "Specify coating requirements for exposed piping in marine environment",
            "Review and update insulation thickness calculations",
            "Provide vendor data sheets for all critical instruments",
            "Add note regarding maintenance access requirements",
            "Clarify foundation design loads for rotating equipment",
        ]
        
        created_docs = []
        created_comments = []
        
        for idx, project in enumerate(projects, 1):
            # Create document
            doc = CRSDocument.objects.create(
                document_name=project['doc_name'],
                document_number=project['doc_number'],
                project_name=project['name'],
                contractor_name=project['contractor'],
                revision_number=f"Rev {idx}",
                status=random.choice(['pending', 'in_review', 'processing', 'completed']),
                total_comments=0,
                resolved_comments=0,
                pending_comments=0,
                uploaded_by=user,
                assigned_to=user,
                notes=f"Document created for {project['name']} project review"
            )
            
            # Create comments for this document
            num_comments = random.randint(5, 10)
            resolved_count = 0
            
            for comment_idx in range(1, num_comments + 1):
                comment_text = random.choice(sample_comments)
                comment_status = random.choice(['open', 'in_progress', 'resolved', 'closed'])
                
                if comment_status in ['resolved', 'closed']:
                    resolved_count += 1
                
                comment = CRSComment.objects.create(
                    document=doc,
                    serial_number=comment_idx,
                    page_number=random.randint(1, 50),
                    clause_number=f"{random.randint(1, 10)}.{random.randint(1, 5)}",
                    comment_text=comment_text,
                    comment_type=random.choice(['red_comment', 'yellow_box', 'annotation']),
                    status=comment_status,
                    priority=random.choice(['low', 'medium', 'high']),
                    color_rgb=[1.0, 0.0, 0.0] if 'red' in comment_text.lower() else [1.0, 1.0, 0.0],
                    contractor_response="We will update the document as requested." if comment_status != 'open' else None,
                    contractor_response_date=timezone.now() if comment_status != 'open' else None,
                    contractor_responder=user if comment_status != 'open' else None,
                    company_response="Acknowledged. Proceed with update." if comment_status in ['resolved', 'closed'] else None,
                    company_response_date=timezone.now() if comment_status in ['resolved', 'closed'] else None,
                    company_responder=user if comment_status in ['resolved', 'closed'] else None,
                    resolved_at=timezone.now() if comment_status in ['resolved', 'closed'] else None
                )
                created_comments.append(comment)
            
            # Update document stats
            doc.total_comments = num_comments
            doc.resolved_comments = resolved_count
            doc.pending_comments = num_comments - resolved_count
            doc.save()
            
            # Create activity log
            CRSActivity.objects.create(
                document=doc,
                action='created',
                description=f'Document "{doc.document_name}" created with {num_comments} comments',
                performed_by=user,
                new_value={'comment_count': num_comments}
            )
            
            created_docs.append(doc)
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ Created document: {doc.document_name} with {num_comments} comments'
                )
            )
        
        # Summary
        self.stdout.write(self.style.SUCCESS(f'\n📊 Summary:'))
        self.stdout.write(f'  - Created {len(created_docs)} CRS documents')
        self.stdout.write(f'  - Created {len(created_comments)} comments')
        self.stdout.write(f'  - Total resolved: {sum(d.resolved_comments for d in created_docs)}')
        self.stdout.write(f'  - Total pending: {sum(d.pending_comments for d in created_docs)}')
        self.stdout.write(self.style.SUCCESS('\n✨ Sample data created successfully!'))
