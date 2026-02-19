"""
Smart Project Collection Administration Command

This management command provides comprehensive administrative tools for the 
Smart Project Collection system, including batch document analysis, project
organization cleanup, and system maintenance utilities.

Usage:
    # Analyze existing documents and organize them into smart collections
    python manage.py smart_project_admin --analyze-existing

    # Clean up and optimize project organization
    python manage.py smart_project_admin --optimize-projects

    # Generate project analytics report
    python manage.py smart_project_admin --analytics-report

    # Batch re-analyze documents with improved AI
    python manage.py smart_project_admin --reanalyze-all

    # Migrate legacy documents to smart collection system
    python manage.py smart_project_admin --migrate-legacy --source-bucket legacy-docs
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db.models import Count, Sum, Q
from django.contrib.auth import get_user_model

from apps.core.models import (
    ProjectCollection,
    ProjectDiscipline,
    SmartProjectDocument,
    CrossDisciplineRecommendation
)
from apps.core.smart_project_collector import get_smart_project_collector
from apps.core.s3_service import get_s3_service

User = get_user_model()


class Command(BaseCommand):
    help = 'Administrative tools for Smart Project Collection system'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.smart_collector = get_smart_project_collector()
        self.s3_service = get_s3_service()
        self.processed_count = 0
        self.failed_count = 0
        self.start_time = None

    def add_arguments(self, parser):
        """Add command line arguments"""
        parser.add_argument(
            '--analyze-existing',
            action='store_true',
            help='Analyze existing S3 documents and organize into smart collections'
        )
        
        parser.add_argument(
            '--optimize-projects',
            action='store_true',
            help='Clean up and optimize existing project organization'
        )
        
        parser.add_argument(
            '--analytics-report',
            action='store_true',
            help='Generate comprehensive analytics report'
        )
        
        parser.add_argument(
            '--reanalyze-all',
            action='store_true',
            help='Re-analyze all documents with improved AI models'
        )
        
        parser.add_argument(
            '--migrate-legacy',
            action='store_true',
            help='Migrate legacy documents from specified source to smart collection'
        )
        
        parser.add_argument(
            '--source-bucket',
            type=str,
            help='Source S3 bucket/prefix for legacy migration'
        )
        
        parser.add_argument(
            '--project-code',
            type=str,
            help='Limit operations to specific project code'
        )
        
        parser.add_argument(
            '--discipline',
            type=str,
            help='Limit operations to specific discipline'
        )
        
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of documents to process in each batch'
        )
        
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes'
        )

    def handle(self, *args, **options):
        """Main command handler"""
        self.start_time = time.time()
        
        try:
            # Validate command combinations
            self._validate_options(options)
            
            if options['analyze_existing']:
                self._analyze_existing_documents(options)
            elif options['optimize_projects']:
                self._optimize_projects(options)
            elif options['analytics_report']:
                self._generate_analytics_report(options)
            elif options['reanalyze_all']:
                self._reanalyze_all_documents(options)
            elif options['migrate_legacy']:
                self._migrate_legacy_documents(options)
            else:
                self.stdout.write(
                    self.style.ERROR('Please specify an operation. Use --help for options.')
                )
                
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING('\nOperation interrupted by user')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Command failed: {str(e)}')
            )
        finally:
            self._print_summary()

    def _validate_options(self, options):
        """Validate command line option combinations"""
        operations = [
            options['analyze_existing'],
            options['optimize_projects'], 
            options['analytics_report'],
            options['reanalyze_all'],
            options['migrate_legacy']
        ]
        
        if sum(operations) != 1:
            raise CommandError('Please specify exactly one operation.')
            
        if options['migrate_legacy'] and not options['source_bucket']:
            raise CommandError('--source-bucket is required when using --migrate-legacy')

    def _analyze_existing_documents(self, options):
        """Analyze existing S3 documents and organize into smart collections"""
        self.stdout.write(
            self.style.SUCCESS('🔍 Starting existing document analysis...')
        )
        
        # Get all existing S3 objects that aren't already in smart collections
        try:
            all_objects = self.s3_service.list_s3_objects(prefix='')
            
            # Filter out objects already in smart collections
            existing_s3_keys = set(
                SmartProjectDocument.objects.filter(
                    is_active=True
                ).values_list('s3_key', flat=True)
            )
            
            unprocessed_objects = [
                obj for obj in all_objects 
                if obj.get('Key') not in existing_s3_keys
                and not obj.get('Key', '').endswith('/')  # Skip folders
            ]
            
            self.stdout.write(f'Found {len(unprocessed_objects)} unprocessed documents')
            
            if options['dry_run']:
                self._show_dry_run_analysis(unprocessed_objects)
                return
            
            # Process in batches
            batch_size = options['batch_size']
            total_batches = (len(unprocessed_objects) + batch_size - 1) // batch_size
            
            for i in range(0, len(unprocessed_objects), batch_size):
                batch = unprocessed_objects[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                
                self.stdout.write(
                    f'Processing batch {batch_num}/{total_batches} '
                    f'({len(batch)} documents)...'
                )
                
                asyncio.run(self._process_document_batch(batch, options))
                
                # Small delay to avoid overwhelming the system
                time.sleep(1)
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error analyzing existing documents: {str(e)}')
            )

    async def _process_document_batch(self, s3_objects, options):
        """Process a batch of S3 objects with smart collection"""
        for s3_obj in s3_objects:
            try:
                s3_key = s3_obj['Key']
                filename = os.path.basename(s3_key)
                
                self.stdout.write(f'  Processing: {filename}')
                
                # Download object temporarily for analysis
                file_content = self.s3_service.download_file_content(s3_key)
                
                # Create a mock file object for the smart collector
                class MockFile:
                    def __init__(self, content, name):
                        self.content = content
                        self.name = name
                        self.size = len(content)
                    
                    def read(self):
                        return self.content
                
                mock_file = MockFile(file_content, filename)
                
                # Run smart collection analysis
                project_doc = await self.smart_collector.collect_and_organize_document(
                    file_obj=mock_file,
                    filename=filename,
                    user_id=1,  # System user
                    hint_project_code=options.get('project_code'),
                    hint_discipline=options.get('discipline')
                )
                
                # Create database records
                self._create_smart_document_record(project_doc, s3_key)
                
                self.processed_count += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f'  Failed to process {s3_obj["Key"]}: {str(e)}')
                )
                self.failed_count += 1

    def _optimize_projects(self, options):
        """Optimize existing project organization"""
        self.stdout.write(
            self.style.SUCCESS('⚙️ Starting project optimization...')
        )
        
        # Get all projects
        projects = ProjectCollection.objects.all()
        if options.get('project_code'):
            projects = projects.filter(project_code=options['project_code'])
            
        self.stdout.write(f'Optimizing {projects.count()} projects...')
        
        for project in projects:
            self.stdout.write(f'Optimizing project: {project.project_code}')
            
            try:
                # Update project statistics
                self._update_project_statistics(project)
                
                # Clean up duplicate documents
                self._remove_duplicate_documents(project)
                
                # Optimize discipline organization
                self._optimize_discipline_structure(project)
                
                # Generate missing recommendations
                self._generate_missing_recommendations(project)
                
                self.processed_count += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f'  Failed to optimize {project.project_code}: {str(e)}')
                )
                self.failed_count += 1

    def _generate_analytics_report(self, options):
        """Generate comprehensive analytics report"""
        self.stdout.write(
            self.style.SUCCESS('📊 Generating analytics report...')
        )
        
        # Collect statistics
        stats = {
            'overview': self._get_overview_stats(),
            'projects': self._get_project_stats(),
            'disciplines': self._get_discipline_stats(),
            'documents': self._get_document_stats(),
            'ai_performance': self._get_ai_performance_stats(),
            'recommendations': self._get_recommendation_stats()
        }
        
        # Generate report
        report_filename = f'smart_project_analytics_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        with open(report_filename, 'w') as f:
            json.dump(stats, f, indent=2, default=str)
            
        self.stdout.write(
            self.style.SUCCESS(f'📄 Analytics report saved to: {report_filename}')
        )
        
        # Print summary to console
        self._print_analytics_summary(stats)

    def _reanalyze_all_documents(self, options):
        """Re-analyze all documents with improved AI models"""
        self.stdout.write(
            self.style.SUCCESS('🤖 Starting document re-analysis...')
        )
        
        # Get documents to reanalyze
        documents = SmartProjectDocument.objects.filter(is_active=True)
        
        if options.get('project_code'):
            documents = documents.filter(project__project_code=options['project_code'])
        if options.get('discipline'):
            documents = documents.filter(discipline__discipline_name=options['discipline'])
            
        total_docs = documents.count()
        self.stdout.write(f'Re-analyzing {total_docs} documents...')
        
        if options['dry_run']:
            self.stdout.write('DRY RUN: Would re-analyze the following documents:')
            for doc in documents[:10]:  # Show first 10
                self.stdout.write(f'  - {doc.filename} ({doc.project.project_code})')
            if total_docs > 10:
                self.stdout.write(f'  ... and {total_docs - 10} more documents')
            return
        
        # Process in batches
        batch_size = options['batch_size'] 
        for i in range(0, total_docs, batch_size):
            batch = documents[i:i + batch_size]
            
            self.stdout.write(f'Processing batch {i//batch_size + 1}...')
            
            asyncio.run(self._reanalyze_document_batch(list(batch)))

    def _migrate_legacy_documents(self, options):
        """Migrate legacy documents to smart collection system"""
        self.stdout.write(
            self.style.SUCCESS(f'📦 Migrating legacy documents from: {options["source_bucket"]}')
        )
        
        source_prefix = options['source_bucket'].rstrip('/')
        
        try:
            # List legacy documents
            legacy_objects = self.s3_service.list_s3_objects(prefix=source_prefix)
            
            self.stdout.write(f'Found {len(legacy_objects)} legacy documents')
            
            if options['dry_run']:
                self.stdout.write('DRY RUN: Would migrate the following documents:')
                for obj in legacy_objects[:10]:
                    self.stdout.write(f'  - {obj["Key"]}')
                if len(legacy_objects) > 10:
                    self.stdout.write(f'  ... and {len(legacy_objects) - 10} more documents')
                return
            
            # Process migration
            asyncio.run(self._migrate_legacy_batch(legacy_objects, options))
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Legacy migration failed: {str(e)}')
            )

    # Helper methods
    
    def _update_project_statistics(self, project):
        """Update project statistics based on current documents"""
        documents = project.documents.filter(is_active=True)
        
        project.total_documents = documents.count()
        project.total_size_bytes = documents.aggregate(
            total=Sum('file_size')
        )['total'] or 0
        
        if documents.exists():
            project.last_document_upload = documents.order_by('-upload_date').first().upload_date
            
        project.save()

    def _create_smart_document_record(self, project_doc, existing_s3_key=None):
        """Create database record for analyzed document"""
        # Get or create project
        project_obj, _ = ProjectCollection.objects.get_or_create(
            project_code=project_doc.project_code,
            defaults={'project_name': project_doc.project_code}
        )
        
        # Get or create discipline  
        discipline_obj, _ = ProjectDiscipline.objects.get_or_create(
            project=project_obj,
            discipline_name=project_doc.discipline,
            defaults={'discipline_type': project_doc.discipline}
        )
        
        # Create document record
        SmartProjectDocument.objects.create(
            document_id=project_doc.document_id,
            filename=project_doc.filename,
            original_filename=project_doc.filename,
            project=project_obj,
            discipline=discipline_obj,
            document_type=project_doc.document_type,
            document_subtype=project_doc.document_subtype,
            s3_key=existing_s3_key or project_doc.organized_s3_key,
            file_size=project_doc.file_size,
            file_extension=project_doc.filename.split('.')[-1].lower(),
            uploaded_by_id=1,  # System user
            ai_classification_confidence=project_doc.confidence_score,
            ai_extracted_metadata=project_doc.extracted_metadata
        )

    def _get_overview_stats(self):
        """Get basic overview statistics"""
        return {
            'total_projects': ProjectCollection.objects.count(),
            'total_documents': SmartProjectDocument.objects.filter(is_active=True).count(),
            'total_disciplines': ProjectDiscipline.objects.count(),
            'total_size_gb': SmartProjectDocument.objects.filter(
                is_active=True
            ).aggregate(total=Sum('file_size'))['total'] or 0 / (1024**3)
        }

    def _print_summary(self):
        """Print operation summary"""
        if self.start_time:
            duration = time.time() - self.start_time
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✅ Operation completed in {duration:.2f} seconds\n'
                    f'   Processed: {self.processed_count}\n'
                    f'   Failed: {self.failed_count}'
                )
            )

    def _print_analytics_summary(self, stats):
        """Print analytics summary to console"""
        self.stdout.write('\n' + '='*50)
        self.stdout.write('SMART PROJECT COLLECTION ANALYTICS SUMMARY')
        self.stdout.write('='*50)
        
        overview = stats['overview']
        self.stdout.write(f'Projects: {overview["total_projects"]}')
        self.stdout.write(f'Documents: {overview["total_documents"]}') 
        self.stdout.write(f'Disciplines: {overview["total_disciplines"]}')
        self.stdout.write(f'Total Size: {overview["total_size_gb"]:.2f} GB')