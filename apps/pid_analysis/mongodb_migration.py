"""
MongoDB Migration Utilities
Smart migration tools to move existing PostgreSQL reports to MongoDB
"""

import logging
from typing import Dict, Any, List
from django.db import transaction

from apps.pid_analysis.models import PIDDrawing, PIDAnalysisReport, PIDIssue
from apps.pid_analysis.hybrid_report_manager import HybridReportManager
from apps.core.mongodb_models import (
    PIDAnalysisReportDocument,
    PIDIssueDocument,
    IssueStatus,
    IssueSeverity,
)
from apps.core.mongodb_service import MongoDBReportService

logger = logging.getLogger(__name__)


class MongoDBMigration:
    """
    Migration utilities for moving data from PostgreSQL to MongoDB
    Maintains PostgreSQL for metadata, MongoDB for full reports
    """
    
    def __init__(self):
        self.hybrid_manager = HybridReportManager()
        self.mongo_service = MongoDBReportService()
    
    def migrate_single_report(self, report_id: int, delete_after: bool = False) -> bool:
        """
        Migrate a single report from PostgreSQL to MongoDB
        
        Args:
            report_id: PostgreSQL report ID
            delete_after: Whether to delete from PostgreSQL after migration
        
        Returns:
            True if successful
        """
        try:
            # Get report from PostgreSQL
            report = PIDAnalysisReport.objects.select_related('pid_drawing').get(id=report_id)
            drawing = report.pid_drawing
            
            # Get all issues
            issues = list(report.issues.all())
            
            # Build analysis result dictionary
            analysis_result = {
                'drawing_info': {
                    'drawing_number': drawing.drawing_number,
                    'drawing_title': drawing.drawing_title,
                    'revision': drawing.revision,
                    'project_name': drawing.project_name,
                },
                'issues': [],
                'summary': {
                    'total_issues': report.total_issues,
                    'approved_count': report.approved_count,
                    'ignored_count': report.ignored_count,
                    'pending_count': report.pending_count,
                },
                **report.report_data  # Include existing report_data
            }
            
            # Convert issues to list format
            for issue in issues:
                analysis_result['issues'].append({
                    'serial_number': issue.serial_number,
                    'pid_reference': issue.pid_reference,
                    'issue_observed': issue.issue_observed,
                    'action_required': issue.action_required,
                    'severity': issue.severity,
                    'category': issue.category,
                    'status': issue.status,
                    'approval': issue.approval,
                    'remark': issue.remark,
                })
            
            # Create in MongoDB
            mongo_report_id = self.hybrid_manager.create_report_with_issues(
                drawing,
                analysis_result,
                drawing.uploaded_by_id
            )
            
            logger.info(f"✅ Migrated report {report_id} to MongoDB: {mongo_report_id}")
            
            # Optionally delete from PostgreSQL
            if delete_after:
                with transaction.atomic():
                    # Store MongoDB ID reference before deletion
                    drawing.mongodb_report_id = mongo_report_id  # Add this field to model if needed
                    drawing.save()
                    
                    # Delete PostgreSQL report and issues (cascade)
                    report.delete()
                    
                logger.info(f"✅ Deleted PostgreSQL report {report_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to migrate report {report_id}: {str(e)}")
            return False
    
    def migrate_all_reports(self, batch_size: int = 10, delete_after: bool = False) -> Dict[str, Any]:
        """
        Migrate all reports from PostgreSQL to MongoDB
        
        Args:
            batch_size: Number of reports to process at a time
            delete_after: Whether to delete from PostgreSQL after migration
        
        Returns:
            Migration statistics
        """
        stats = {
            'total': 0,
            'migrated': 0,
            'failed': 0,
            'skipped': 0,
        }
        
        try:
            # Get all reports
            reports = PIDAnalysisReport.objects.all().select_related('pid_drawing')
            stats['total'] = reports.count()
            
            logger.info(f"Starting migration of {stats['total']} reports...")
            
            for i, report in enumerate(reports):
                try:
                    # Check if already migrated to MongoDB
                    existing = self.mongo_service.get_report_by_drawing_id(report.pid_drawing.id)
                    if existing:
                        logger.info(f"Skipping report {report.id} - already in MongoDB")
                        stats['skipped'] += 1
                        continue
                    
                    # Migrate
                    success = self.migrate_single_report(report.id, delete_after)
                    
                    if success:
                        stats['migrated'] += 1
                    else:
                        stats['failed'] += 1
                    
                    # Log progress
                    if (i + 1) % batch_size == 0:
                        logger.info(f"Progress: {i + 1}/{stats['total']} reports processed")
                    
                except Exception as e:
                    logger.error(f"Failed to migrate report {report.id}: {str(e)}")
                    stats['failed'] += 1
            
            logger.info(f"✅ Migration complete: {stats}")
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Migration failed: {str(e)}")
            return stats
    
    def verify_migration(self, drawing_id: int) -> Dict[str, Any]:
        """
        Verify that a report was migrated correctly
        
        Args:
            drawing_id: Drawing ID to verify
        
        Returns:
            Verification results
        """
        try:
            # Get from PostgreSQL
            pg_report = PIDAnalysisReport.objects.filter(
                pid_drawing_id=drawing_id
            ).first()
            
            # Get from MongoDB
            mongo_report = self.hybrid_manager.get_report_for_drawing(drawing_id)
            
            results = {
                'drawing_id': drawing_id,
                'in_postgresql': pg_report is not None,
                'in_mongodb': mongo_report is not None,
                'issues_match': False,
                'data_match': False,
            }
            
            if pg_report and mongo_report:
                # Compare issue counts
                pg_issues_count = pg_report.issues.count()
                mongo_issues_count = len(mongo_report.get('issues', []))
                results['issues_match'] = pg_issues_count == mongo_issues_count
                
                # Compare totals
                results['data_match'] = (
                    pg_report.total_issues == mongo_report['total_issues']
                    and pg_report.approved_count == mongo_report['approved_count']
                    and pg_report.pending_count == mongo_report['pending_count']
                )
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Verification failed: {str(e)}")
            return {'error': str(e)}
    
    def rollback_migration(self, drawing_id: int) -> bool:
        """
        Rollback: Delete MongoDB report for a drawing
        
        Args:
            drawing_id: Drawing ID
        
        Returns:
            True if successful
        """
        try:
            # Get MongoDB report
            report = self.mongo_service.get_report_by_drawing_id(drawing_id)
            
            if report:
                # Delete from MongoDB
                success = self.hybrid_manager.delete_report(str(report._id))
                
                if success:
                    logger.info(f"✅ Rolled back MongoDB report for drawing {drawing_id}")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Rollback failed: {str(e)}")
            return False


# Utility function for management commands
def migrate_reports_to_mongodb(delete_after: bool = False, batch_size: int = 10) -> Dict[str, Any]:
    """
    Convenience function to migrate all reports
    
    Usage:
        from apps.pid_analysis.mongodb_migration import migrate_reports_to_mongodb
        stats = migrate_reports_to_mongodb(delete_after=False)
    """
    migration = MongoDBMigration()
    return migration.migrate_all_reports(batch_size, delete_after)
