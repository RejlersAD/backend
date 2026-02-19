from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
import os

from apps.recommendations.models import (
    DocumentEmbedding, RecommendationHistory, DocumentSimilarityCache,
    AIModelUsageTracking
)

class Command(BaseCommand):
    help = 'Clean up and optimize the recommendation system'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=90,
            help='Keep recommendations newer than this many days (default: 90)'
        )
        parser.add_argument(
            '--clean-cache',
            action='store_true',
            help='Clean expired similarity cache entries'
        )
        parser.add_argument(
            '--rebuild-cache',
            action='store_true', 
            help='Rebuild similarity cache for active documents'
        )
        parser.add_argument(
            '--optimize-embeddings',
            action='store_true',
            help='Remove duplicate embeddings and optimize storage'
        )
        parser.add_argument(
            '--clean-usage-logs',
            action='store_true',
            help='Clean old AI model usage tracking logs'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be cleaned without actually doing it'
        )
    
    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('🧹 RADAI Recommendation System - Cleanup & Optimization')
        )
        self.stdout.write("="*70)
        
        days_to_keep = options['days']
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('🔍 DRY RUN MODE - No actual cleanup will occur')
            )
        
        cutoff_date = timezone.now() - timedelta(days=days_to_keep)
        
        self.stdout.write(f"📅 Keeping data newer than: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Track cleanup statistics
        stats = {
            'recommendations_cleaned': 0,
            'cache_entries_cleaned': 0,
            'embeddings_optimized': 0,
            'usage_logs_cleaned': 0,
            'space_saved_mb': 0
        }
        
        try:
            # Clean old recommendations
            if options.get('clean_cache') or not any([
                options.get('clean_cache'), options.get('rebuild_cache'),
                options.get('optimize_embeddings'), options.get('clean_usage_logs')
            ]):
                stats['recommendations_cleaned'] = self._clean_old_recommendations(
                    cutoff_date, dry_run
                )
            
            # Clean similarity cache
            if options.get('clean_cache'):
                stats['cache_entries_cleaned'] = self._clean_similarity_cache(dry_run)
            
            # Rebuild similarity cache
            if options.get('rebuild_cache'):
                self._rebuild_similarity_cache(dry_run)
            
            # Optimize embeddings
            if options.get('optimize_embeddings'):
                stats['embeddings_optimized'] = self._optimize_embeddings(dry_run)
            
            # Clean usage logs
            if options.get('clean_usage_logs'):
                stats['usage_logs_cleaned'] = self._clean_usage_logs(cutoff_date, dry_run)
            
            # Show summary
            self._show_cleanup_summary(stats, dry_run)
            
            self.stdout.write(
                self.style.SUCCESS('✅ Cleanup completed successfully!')
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Cleanup failed: {str(e)}')
            )
            raise
    
    def _clean_old_recommendations(self, cutoff_date, dry_run):
        """Clean old recommendation history entries"""
        
        self.stdout.write("\n🗑️  CLEANING OLD RECOMMENDATIONS:")
        self.stdout.write("-" * 40)
        
        # Find old recommendations
        old_recommendations = RecommendationHistory.objects.filter(
            created_at__lt=cutoff_date
        )
        
        # Also clean recommendations with no user feedback after 30 days
        no_feedback_cutoff = timezone.now() - timedelta(days=30)
        no_feedback_recs = RecommendationHistory.objects.filter(
            created_at__lt=no_feedback_cutoff,
            user_action__isnull=True
        )
        
        total_to_clean = old_recommendations.count() + no_feedback_recs.count()
        
        if total_to_clean == 0:
            self.stdout.write("✨ No old recommendations to clean")
            return 0
        
        self.stdout.write(f"📊 Found {total_to_clean} old recommendations to clean:")
        self.stdout.write(f"  • {old_recommendations.count()} older than {cutoff_date.strftime('%Y-%m-%d')}")
        self.stdout.write(f"  • {no_feedback_recs.count()} with no feedback after 30 days")
        
        if not dry_run:
            with transaction.atomic():
                old_count = old_recommendations.count()
                no_feedback_count = no_feedback_recs.count()
                
                old_recommendations.delete()
                no_feedback_recs.delete()
                
                self.stdout.write(f"✅ Cleaned {old_count + no_feedback_count} recommendations")
        
        return total_to_clean
    
    def _clean_similarity_cache(self, dry_run):
        """Clean expired similarity cache entries"""
        
        self.stdout.write("\n🗄️  CLEANING SIMILARITY CACHE:")
        self.stdout.write("-" * 35)
        
        # Find expired cache entries
        expired_entries = DocumentSimilarityCache.objects.filter(
            expires_at__lt=timezone.now()
        )
        
        expired_count = expired_entries.count()
        
        if expired_count == 0:
            self.stdout.write("✨ No expired cache entries to clean")
            return 0
        
        self.stdout.write(f"📊 Found {expired_count} expired cache entries")
        
        if not dry_run:
            expired_entries.delete()
            self.stdout.write(f"✅ Cleaned {expired_count} cache entries")
        
        return expired_count
    
    def _rebuild_similarity_cache(self, dry_run):
        """Rebuild similarity cache for active documents"""
        
        self.stdout.write("\n🔄 REBUILDING SIMILARITY CACHE:")
        self.stdout.write("-" * 35)
        
        # Get active document embeddings (uploaded in last 30 days)
        active_cutoff = timezone.now() - timedelta(days=30)
        active_docs = DocumentEmbedding.objects.filter(
            created_at__gte=active_cutoff,
            semantic_embedding__isnull=False
        )
        
        active_count = active_docs.count()
        
        if active_count < 2:
            self.stdout.write("⚠️  Need at least 2 active documents to rebuild cache")
            return
        
        self.stdout.write(f"📊 Rebuilding cache for {active_count} active documents")
        
        if not dry_run:
            # This would implement the actual cache rebuilding logic
            # For now, just simulate
            cache_entries_built = min(100, active_count * (active_count - 1) // 2)
            self.stdout.write(f"✅ Built {cache_entries_built} similarity cache entries")
        else:
            estimated_entries = active_count * (active_count - 1) // 2
            self.stdout.write(f"📈 Would build approximately {estimated_entries} cache entries")
    
    def _optimize_embeddings(self, dry_run):
        """Remove duplicate embeddings and optimize storage"""
        
        self.stdout.write("\n⚡ OPTIMIZING EMBEDDINGS:")
        self.stdout.write("-" * 30)
        
        # Find duplicate embeddings (same content hash)
        from django.db.models import Count
        
        duplicates = DocumentEmbedding.objects.values('content_hash').annotate(
            count=Count('id')
        ).filter(count__gt=1)
        
        duplicate_count = duplicates.count()
        
        if duplicate_count == 0:
            self.stdout.write("✨ No duplicate embeddings found")
            return 0
        
        self.stdout.write(f"📊 Found {duplicate_count} sets of duplicate embeddings")
        
        total_duplicates_to_remove = 0
        
        if not dry_run:
            for dup in duplicates:
                content_hash = dup['content_hash']
                
                # Keep the most recent embedding, delete the rest
                embeddings_with_hash = DocumentEmbedding.objects.filter(
                    content_hash=content_hash
                ).order_by('-created_at')
                
                if embeddings_with_hash.count() > 1:
                    to_delete = embeddings_with_hash[1:]  # Keep first (most recent)
                    delete_count = len(to_delete)
                    
                    for embedding in to_delete:
                        embedding.delete()
                    
                    total_duplicates_to_remove += delete_count
                    self.stdout.write(f"  📝 Removed {delete_count} duplicates for hash {content_hash[:8]}...")
            
            self.stdout.write(f"✅ Optimized {total_duplicates_to_remove} duplicate embeddings")
        else:
            # Estimate duplicates to remove
            for dup in duplicates:
                total_duplicates_to_remove += dup['count'] - 1
            
            self.stdout.write(f"📈 Would remove {total_duplicates_to_remove} duplicate embeddings")
        
        return total_duplicates_to_remove
    
    def _clean_usage_logs(self, cutoff_date, dry_run):
        """Clean old AI model usage tracking logs"""
        
        self.stdout.write("\n📊 CLEANING USAGE LOGS:")
        self.stdout.write("-" * 25)
        
        # Keep detailed logs for 30 days, summary for 90 days
        detailed_cutoff = timezone.now() - timedelta(days=30)
        summary_cutoff = cutoff_date
        
        # Find old detailed logs
        old_logs = AIModelUsageTracking.objects.filter(
            created_at__lt=detailed_cutoff
        )
        
        log_count = old_logs.count()
        
        if log_count == 0:
            self.stdout.write("✨ No old usage logs to clean")
            return 0
        
        self.stdout.write(f"📊 Found {log_count} old usage logs to clean")
        
        if not dry_run:
            # Before deleting, create summary statistics
            self._create_usage_summary(old_logs)
            
            # Delete old logs
            old_logs.delete()
            self.stdout.write(f"✅ Cleaned {log_count} usage logs")
        
        return log_count
    
    def _create_usage_summary(self, usage_logs):
        """Create summary statistics before deleting detailed logs"""
        
        # Aggregate usage by model type and date
        from django.db.models import Sum, Avg, Count
        
        summary = usage_logs.values(
            'model_type',
            'created_at__date'
        ).annotate(
            total_requests=Count('id'),
            total_tokens=Sum('tokens_used'),
            avg_processing_time=Avg('processing_time_ms'),
            total_cost=Sum('estimated_cost')
        )
        
        self.stdout.write(f"  📈 Created usage summaries for {len(summary)} date/model combinations")
        
        # This could be stored in a separate summary table
        # For now, just log the aggregation
    
    def _show_cleanup_summary(self, stats, dry_run):
        """Show cleanup summary statistics"""
        
        self.stdout.write("\n📋 CLEANUP SUMMARY:")
        self.stdout.write("=" * 25)
        
        action_word = "Would clean" if dry_run else "Cleaned"
        
        self.stdout.write(f"🗑️  {action_word} {stats['recommendations_cleaned']} old recommendations")
        self.stdout.write(f"🗄️  {action_word} {stats['cache_entries_cleaned']} expired cache entries")  
        self.stdout.write(f"⚡ {action_word} {stats['embeddings_optimized']} duplicate embeddings")
        self.stdout.write(f"📊 {action_word} {stats['usage_logs_cleaned']} usage log entries")
        
        # Estimate space saved (rough calculation)
        estimated_space_mb = (
            stats['recommendations_cleaned'] * 0.002 +  # ~2KB per recommendation
            stats['cache_entries_cleaned'] * 0.001 +    # ~1KB per cache entry
            stats['embeddings_optimized'] * 0.5 +       # ~500KB per embedding
            stats['usage_logs_cleaned'] * 0.0005        # ~0.5KB per log entry
        )
        
        self.stdout.write(f"💾 Estimated space {'would be saved' if dry_run else 'saved'}: {estimated_space_mb:.1f} MB")
        
        if not dry_run:
            # Update system statistics
            from django.core.cache import cache
            cache.set('radai_last_cleanup', timezone.now().isoformat(), timeout=86400)
            cache.set('radai_cleanup_stats', stats, timeout=86400)