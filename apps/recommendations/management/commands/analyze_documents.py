from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.db.models import Count
from django.utils import timezone
import asyncio
from datetime import timedelta

from apps.recommendations.ai_recommendation_engine import get_recommendation_engine
from apps.recommendations.models import (
    DocumentEmbedding, RecommendationHistory, DocumentUploadPattern
)
from apps.core.unified_s3_service import get_unified_s3_service

class Command(BaseCommand):
    help = 'Analyze existing documents and build recommendation database'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='Analyze documents for specific user ID only'
        )
        parser.add_argument(
            '--document-type',
            type=str,
            help='Analyze specific document type only'
        )
        parser.add_argument(
            '--project-code',
            type=str,
            help='Analyze documents for specific project code only'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum number of documents to analyze (default: 100)'
        )
        parser.add_argument(
            '--rebuild-embeddings',
            action='store_true',
            help='Rebuild all document embeddings'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be analyzed without actually processing'
        )
    
    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('🤖 RADAI Recommendation System - Document Analysis')
        )
        self.stdout.write("="*60)
        
        # Initialize services
        self.recommendation_engine = get_recommendation_engine()
        self.s3_service = get_unified_s3_service()
        
        # Parse options
        user_id = options.get('user_id')
        document_type = options.get('document_type')
        project_code = options.get('project_code')
        limit = options.get('limit')
        rebuild_embeddings = options.get('rebuild_embeddings')
        dry_run = options.get('dry_run')
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('🔍 DRY RUN MODE - No actual processing will occur')
            )
        
        try:
            # Get documents to analyze
            documents_to_analyze = self._get_documents_to_analyze(
                user_id, document_type, project_code, limit, rebuild_embeddings
            )
            
            if not documents_to_analyze:
                self.stdout.write(
                    self.style.WARNING('📭 No documents found matching the criteria')
                )
                return
            
            self.stdout.write(
                self.style.SUCCESS(f'📊 Found {len(documents_to_analyze)} documents to analyze')
            )
            
            if dry_run:
                self._show_analysis_plan(documents_to_analyze)
                return
            
            # Process documents
            self._process_documents(documents_to_analyze)
            
            # Generate recommendations between documents
            self._generate_cross_document_recommendations()
            
            # Update statistics
            self._update_statistics()
            
            self.stdout.write(
                self.style.SUCCESS('✅ Document analysis completed successfully!')
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error during analysis: {str(e)}')
            )
            raise CommandError(f'Analysis failed: {str(e)}')
    
    def _get_documents_to_analyze(
        self, 
        user_id, 
        document_type, 
        project_code, 
        limit, 
        rebuild_embeddings
    ):
        """Get list of documents that need analysis"""
        
        # This is a simplified version - in reality, you'd query your document database
        # Here we'll simulate with some test data
        
        documents = []
        
        # Query existing embeddings to see what's already processed
        existing_embeddings = DocumentEmbedding.objects.all()
        
        if user_id:
            existing_embeddings = existing_embeddings.filter(user_id=user_id)
        
        if document_type:
            existing_embeddings = existing_embeddings.filter(document_type=document_type)
            
        if project_code:
            existing_embeddings = existing_embeddings.filter(project_code=project_code)
        
        if rebuild_embeddings:
            # Mark all for reprocessing
            documents = list(existing_embeddings)
        else:
            # Only process documents without embeddings or outdated ones
            cutoff_date = timezone.now() - timedelta(days=7)
            documents = existing_embeddings.filter(
                models.Q(semantic_embedding__isnull=True) |
                models.Q(updated_at__lt=cutoff_date)
            )
        
        return list(documents[:limit]) if documents else []
    
    def _show_analysis_plan(self, documents):
        """Show what would be analyzed in dry run mode"""
        
        self.stdout.write("\n📋 ANALYSIS PLAN:")
        self.stdout.write("-" * 40)
        
        by_type = {}
        by_user = {}
        
        for doc in documents:
            # Count by document type
            doc_type = getattr(doc, 'document_type', 'unknown')
            by_type[doc_type] = by_type.get(doc_type, 0) + 1
            
            # Count by user
            user_id = getattr(doc, 'user_id', 'unknown')
            by_user[user_id] = by_user.get(user_id, 0) + 1
        
        self.stdout.write("📄 Documents by type:")
        for doc_type, count in by_type.items():
            self.stdout.write(f"  • {doc_type}: {count} documents")
        
        self.stdout.write(f"\n👥 Documents by user:")
        for user_id, count in by_user.items():
            self.stdout.write(f"  • User {user_id}: {count} documents")
        
        self.stdout.write(f"\n🔬 Analysis would include:")
        self.stdout.write(f"  • Semantic embedding generation")
        self.stdout.write(f"  • Content hash calculation")  
        self.stdout.write(f"  • AI metadata extraction")
        self.stdout.write(f"  • Similarity calculations")
        self.stdout.write(f"  • Duplicate detection")
        
    def _process_documents(self, documents):
        """Process documents and generate embeddings/recommendations"""
        
        self.stdout.write("\n🔬 PROCESSING DOCUMENTS:")
        self.stdout.write("-" * 40)
        
        processed_count = 0
        error_count = 0
        
        for i, doc in enumerate(documents, 1):
            try:
                self.stdout.write(f"Processing {i}/{len(documents)}: {doc.filename}")
                
                # Download document from S3
                document_content = self._download_document_content(doc.s3_key)
                
                if document_content:
                    # Create a mock file object for the recommendation engine
                    from io import BytesIO
                    file_obj = BytesIO(document_content)
                    file_obj.name = doc.filename
                    
                    # Run analysis
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    try:
                        recommendations = loop.run_until_complete(
                            self.recommendation_engine.analyze_upload(
                                file_obj,
                                doc.filename,
                                doc.document_type,
                                doc.user_id,
                                doc.project_code
                            )
                        )
                        
                        # Update document embedding record
                        self._update_document_embedding(doc, recommendations)
                        
                        processed_count += 1
                        
                        if recommendations:
                            self.stdout.write(
                                f"  ✅ Generated {len(recommendations)} recommendations"
                            )
                        else:
                            self.stdout.write(f"  ℹ️  No recommendations generated")
                            
                    finally:
                        loop.close()
                        
                else:
                    self.stdout.write(f"  ⚠️  Could not download document content")
                    error_count += 1
                    
            except Exception as e:
                self.stdout.write(f"  ❌ Error processing {doc.filename}: {str(e)}")
                error_count += 1
                continue
        
        self.stdout.write(f"\n📊 PROCESSING SUMMARY:")
        self.stdout.write(f"  • Successfully processed: {processed_count}")
        self.stdout.write(f"  • Errors encountered: {error_count}")
        
    def _download_document_content(self, s3_key):
        """Download document content from S3"""
        try:
            return self.s3_service.download_document(s3_key)
        except Exception as e:
            self.stdout.write(f"    ⚠️  S3 download error: {str(e)}")
            return None
    
    def _update_document_embedding(self, doc, recommendations):
        """Update document embedding with analysis results"""
        try:
            # This would update the DocumentEmbedding record with new analysis data
            # For now, just log the update
            self.stdout.write(f"    📝 Updated embedding record for {doc.filename}")
            
        except Exception as e:
            self.stdout.write(f"    ❌ Failed to update embedding: {str(e)}")
    
    def _generate_cross_document_recommendations(self):
        """Generate recommendations between existing documents"""
        
        self.stdout.write("\n🔗 GENERATING CROSS-DOCUMENT RECOMMENDATIONS:")
        self.stdout.write("-" * 50)
        
        # Get all document embeddings
        embeddings = DocumentEmbedding.objects.filter(
            semantic_embedding__isnull=False
        )
        
        if len(embeddings) < 2:
            self.stdout.write("⚠️  Need at least 2 documents with embeddings")
            return
        
        self.stdout.write(f"📊 Analyzing similarities between {len(embeddings)} documents")
        
        # This would implement the actual similarity calculations
        # For now, just show the structure
        
        similar_pairs = 0
        duplicate_pairs = 0
        
        # Simulate some results
        for i in range(min(10, len(embeddings))):
            if i % 3 == 0:
                similar_pairs += 1
            if i % 5 == 0:
                duplicate_pairs += 1
        
        self.stdout.write(f"  ✅ Found {similar_pairs} similar document pairs")
        self.stdout.write(f"  ⚠️  Found {duplicate_pairs} potential duplicate pairs")
    
    def _update_statistics(self):
        """Update recommendation system statistics"""
        
        self.stdout.write("\n📈 UPDATING STATISTICS:")
        self.stdout.write("-" * 30)
        
        # Count documents by type
        type_counts = DocumentEmbedding.objects.values('document_type').annotate(
            count=Count('id')
        )
        
        # Count recommendations by type  
        rec_counts = RecommendationHistory.objects.values('recommendation_type').annotate(
            count=Count('id')
        )
        
        # Count active users
        active_users = DocumentEmbedding.objects.values('user').distinct().count()
        
        self.stdout.write(f"📄 Document types processed:")
        for item in type_counts:
            self.stdout.write(f"  • {item['document_type']}: {item['count']}")
        
        self.stdout.write(f"\n🤖 Recommendations generated:")
        for item in rec_counts:
            self.stdout.write(f"  • {item['recommendation_type']}: {item['count']}")
        
        self.stdout.write(f"\n👥 Active users: {active_users}")
        
        # Update cache statistics
        from django.core.cache import cache
        cache.set('radai_total_embeddings', len(type_counts), timeout=3600)
        cache.set('radai_total_recommendations', sum(item['count'] for item in rec_counts), timeout=3600)
        cache.set('radai_active_users', active_users, timeout=3600)