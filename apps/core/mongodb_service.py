"""
MongoDB Service Layer for AIFlow
Smart service classes for MongoDB operations with error handling and logging
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from bson import ObjectId

from apps.core.mongodb_client import get_mongodb_collection
from apps.core.mongodb_models import (
    PIDAnalysisReportDocument,
    PIDIssueDocument,
    ReferenceDocumentDocument,
    AnalysisLogDocument,
    IssueStatus,
    IssueSeverity,
)

logger = logging.getLogger(__name__)


class MongoDBReportService:
    """Service for managing P&ID analysis reports in MongoDB"""
    
    def __init__(self):
        self.reports_collection = get_mongodb_collection('pid_reports')
        self.issues_collection = get_mongodb_collection('pid_issues')
        self.logs_collection = get_mongodb_collection('analysis_logs')
    
    def create_report(self, report_doc: PIDAnalysisReportDocument) -> str:
        """
        Create a new analysis report
        
        Args:
            report_doc: PIDAnalysisReportDocument instance
        
        Returns:
            MongoDB document ID as string
        """
        try:
            report_dict = report_doc.to_dict()
            result = self.reports_collection.insert_one(report_dict)
            
            logger.info(f"✅ Report created in MongoDB: {result.inserted_id}")
            
            # Log the creation
            self._log_action(
                report_doc.drawing_id,
                report_doc.user_id,
                'report_created',
                f'Report created for drawing {report_doc.drawing_number}'
            )
            
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error(f"❌ Failed to create report in MongoDB: {str(e)}")
            raise
    
    def get_report_by_drawing_id(self, drawing_id: int) -> Optional[PIDAnalysisReportDocument]:
        """
        Get report by drawing ID
        
        Args:
            drawing_id: PostgreSQL drawing ID
        
        Returns:
            PIDAnalysisReportDocument or None
        """
        try:
            report_data = self.reports_collection.find_one({'drawing_id': drawing_id})
            
            if report_data:
                return PIDAnalysisReportDocument.from_dict(report_data)
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Failed to get report: {str(e)}")
            return None
    
    def get_report_by_id(self, report_id: str) -> Optional[PIDAnalysisReportDocument]:
        """Get report by MongoDB ID"""
        try:
            report_data = self.reports_collection.find_one({'_id': ObjectId(report_id)})
            
            if report_data:
                return PIDAnalysisReportDocument.from_dict(report_data)
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Failed to get report by ID: {str(e)}")
            return None
    
    def update_report(self, report_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update report fields
        
        Args:
            report_id: MongoDB document ID
            updates: Dictionary of fields to update
        
        Returns:
            True if successful
        """
        try:
            updates['updated_at'] = datetime.utcnow()
            
            result = self.reports_collection.update_one(
                {'_id': ObjectId(report_id)},
                {'$set': updates}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Report updated: {report_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Failed to update report: {str(e)}")
            return False
    
    def get_user_reports(self, user_id: int, limit: int = 50) -> List[PIDAnalysisReportDocument]:
        """
        Get reports by user with pagination
        
        Args:
            user_id: User ID
            limit: Maximum number of reports to return
        
        Returns:
            List of PIDAnalysisReportDocument
        """
        try:
            reports_data = self.reports_collection.find(
                {'user_id': user_id}
            ).sort('generated_at', -1).limit(limit)
            
            return [PIDAnalysisReportDocument.from_dict(r) for r in reports_data]
            
        except Exception as e:
            logger.error(f"❌ Failed to get user reports: {str(e)}")
            return []
    
    def delete_report(self, report_id: str) -> bool:
        """Delete report and associated issues"""
        try:
            # Delete associated issues first
            self.issues_collection.delete_many({'report_id': report_id})
            
            # Delete report
            result = self.reports_collection.delete_one({'_id': ObjectId(report_id)})
            
            if result.deleted_count > 0:
                logger.info(f"✅ Report deleted: {report_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Failed to delete report: {str(e)}")
            return False
    
    # Issue Management
    
    def create_issues(self, issues: List[PIDIssueDocument]) -> int:
        """
        Create multiple issues
        
        Args:
            issues: List of PIDIssueDocument
        
        Returns:
            Number of issues created
        """
        try:
            if not issues:
                return 0
            
            issues_data = [issue.to_dict() for issue in issues]
            result = self.issues_collection.insert_many(issues_data)
            
            count = len(result.inserted_ids)
            logger.info(f"✅ Created {count} issues in MongoDB")
            
            return count
            
        except Exception as e:
            logger.error(f"❌ Failed to create issues: {str(e)}")
            return 0
    
    def get_report_issues(self, report_id: str) -> List[PIDIssueDocument]:
        """Get all issues for a report"""
        try:
            issues_data = self.issues_collection.find(
                {'report_id': report_id}
            ).sort('serial_number', 1)
            
            return [PIDIssueDocument.from_dict(i) for i in issues_data]
            
        except Exception as e:
            logger.error(f"❌ Failed to get issues: {str(e)}")
            return []
    
    def update_issue_status(self, issue_id: str, status: str, approval: str = '', remark: str = '') -> bool:
        """Update issue review status"""
        try:
            updates = {
                'status': status,
                'updated_at': datetime.utcnow()
            }
            
            if approval:
                updates['approval'] = approval
            if remark:
                updates['remark'] = remark
            
            result = self.issues_collection.update_one(
                {'_id': ObjectId(issue_id)},
                {'$set': updates}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"❌ Failed to update issue status: {str(e)}")
            return False
    
    def get_issues_by_status(self, report_id: str, status: str) -> List[PIDIssueDocument]:
        """Get issues filtered by status"""
        try:
            issues_data = self.issues_collection.find({
                'report_id': report_id,
                'status': status
            }).sort('serial_number', 1)
            
            return [PIDIssueDocument.from_dict(i) for i in issues_data]
            
        except Exception as e:
            logger.error(f"❌ Failed to get issues by status: {str(e)}")
            return []
    
    def update_report_summary(self, report_id: str) -> bool:
        """Recalculate and update report summary counts"""
        try:
            # Get all issues for this report
            issues = self.get_report_issues(report_id)
            
            # Calculate counts
            total = len(issues)
            approved = sum(1 for i in issues if i.status == IssueStatus.APPROVED.value)
            ignored = sum(1 for i in issues if i.status == IssueStatus.IGNORED.value)
            pending = sum(1 for i in issues if i.status == IssueStatus.PENDING.value)
            
            # Severity counts
            critical = sum(1 for i in issues if i.severity == IssueSeverity.CRITICAL.value)
            major = sum(1 for i in issues if i.severity == IssueSeverity.MAJOR.value)
            minor = sum(1 for i in issues if i.severity == IssueSeverity.MINOR.value)
            observation = sum(1 for i in issues if i.severity == IssueSeverity.OBSERVATION.value)
            
            # Update report
            updates = {
                'total_issues': total,
                'approved_count': approved,
                'ignored_count': ignored,
                'pending_count': pending,
                'critical_count': critical,
                'major_count': major,
                'minor_count': minor,
                'observation_count': observation,
                'updated_at': datetime.utcnow()
            }
            
            return self.update_report(report_id, updates)
            
        except Exception as e:
            logger.error(f"❌ Failed to update report summary: {str(e)}")
            return False
    
    # Logging
    
    def _log_action(self, drawing_id: int, user_id: int, action: str, message: str, **context):
        """Internal method to log actions"""
        try:
            log_doc = AnalysisLogDocument(
                drawing_id=drawing_id,
                user_id=user_id,
                action=action,
                message=message,
                context=context
            )
            
            self.logs_collection.insert_one(log_doc.to_dict())
            
        except Exception as e:
            logger.error(f"Failed to log action: {str(e)}")
    
    def get_analysis_logs(self, drawing_id: int, limit: int = 100) -> List[AnalysisLogDocument]:
        """Get analysis logs for a drawing"""
        try:
            logs_data = self.logs_collection.find(
                {'drawing_id': drawing_id}
            ).sort('timestamp', -1).limit(limit)
            
            return [AnalysisLogDocument.from_dict(log) for log in logs_data]
            
        except Exception as e:
            logger.error(f"❌ Failed to get logs: {str(e)}")
            return []


class MongoDBReferenceDocService:
    """Service for managing reference documents in MongoDB"""
    
    def __init__(self):
        self.collection = get_mongodb_collection('reference_docs')
    
    def create_document(self, doc: ReferenceDocumentDocument) -> str:
        """Create a new reference document"""
        try:
            result = self.collection.insert_one(doc.to_dict())
            logger.info(f"✅ Reference document created: {result.inserted_id}")
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error(f"❌ Failed to create reference document: {str(e)}")
            raise
    
    def get_active_documents(self, category: Optional[str] = None) -> List[ReferenceDocumentDocument]:
        """Get active reference documents, optionally filtered by category"""
        try:
            query = {'is_active': True}
            if category:
                query['category'] = category
            
            docs_data = self.collection.find(query).sort('created_at', -1)
            
            return [ReferenceDocumentDocument.from_dict(d) for d in docs_data]
            
        except Exception as e:
            logger.error(f"❌ Failed to get reference documents: {str(e)}")
            return []
    
    def update_embedding_status(self, doc_id: str, status: str, vector_ids: List[str] = None) -> bool:
        """Update embedding status for a document"""
        try:
            updates = {
                'embedding_status': status,
                'updated_at': datetime.utcnow()
            }
            
            if vector_ids:
                updates['vector_db_ids'] = vector_ids
            
            result = self.collection.update_one(
                {'_id': ObjectId(doc_id)},
                {'$set': updates}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"❌ Failed to update embedding status: {str(e)}")
            return False
