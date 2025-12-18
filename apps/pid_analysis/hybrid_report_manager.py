"""
Hybrid MongoDB Integration for PID Analysis Views
Smart integration that uses PostgreSQL for structured data and MongoDB for reports
"""

import logging
from datetime import datetime
from typing import Dict, Any, List

from apps.core.mongodb_service import MongoDBReportService
from apps.core.mongodb_models import (
    PIDAnalysisReportDocument,
    PIDIssueDocument,
    IssueStatus,
    IssueSeverity,
)

logger = logging.getLogger(__name__)


class HybridReportManager:
    """
    Smart hybrid manager for P&ID reports
    - PostgreSQL: Drawing metadata, user data, basic status
    - MongoDB: Full report data, issues, analysis results (unstructured)
    """
    
    def __init__(self):
        self.mongo_service = MongoDBReportService()
    
    def create_report_with_issues(
        self,
        drawing_obj,  # PIDDrawing instance from PostgreSQL
        analysis_result: Dict[str, Any],
        user_id: int
    ) -> str:
        """
        Create report and issues in MongoDB, keep metadata in PostgreSQL
        
        Args:
            drawing_obj: PIDDrawing model instance (PostgreSQL)
            analysis_result: Full analysis results dictionary
            user_id: User ID
        
        Returns:
            MongoDB report ID
        """
        try:
            # Extract issues from analysis result
            issues_data = analysis_result.get('issues', [])
            drawing_info = analysis_result.get('drawing_info', {})
            
            # Calculate summary statistics
            total_issues = len(issues_data)
            approved_count = sum(1 for i in issues_data if i.get('status') == 'approved')
            ignored_count = sum(1 for i in issues_data if i.get('status') == 'ignored')
            pending_count = total_issues - approved_count - ignored_count
            
            # Severity counts
            critical_count = sum(1 for i in issues_data if i.get('severity') == 'critical')
            major_count = sum(1 for i in issues_data if i.get('severity') == 'major')
            minor_count = sum(1 for i in issues_data if i.get('severity') == 'minor')
            observation_count = sum(1 for i in issues_data if i.get('severity') == 'observation')
            
            # Create MongoDB report document
            report_doc = PIDAnalysisReportDocument(
                drawing_id=drawing_obj.id,
                user_id=user_id,
                drawing_number=drawing_obj.drawing_number or drawing_info.get('drawing_number', ''),
                drawing_title=drawing_obj.drawing_title or drawing_info.get('drawing_title', ''),
                revision=drawing_obj.revision or drawing_info.get('revision', ''),
                project_name=drawing_obj.project_name or drawing_info.get('project_name', ''),
                original_filename=drawing_obj.original_filename,
                total_issues=total_issues,
                approved_count=approved_count,
                ignored_count=ignored_count,
                pending_count=pending_count,
                critical_count=critical_count,
                major_count=major_count,
                minor_count=minor_count,
                observation_count=observation_count,
                report_data=analysis_result,
                drawing_file_url=drawing_obj.file.url if drawing_obj.file else None,
                status='completed',
                rag_enabled=analysis_result.get('rag_enabled', False),
                reference_docs_used=analysis_result.get('reference_docs_used', []),
            )
            
            # Save report to MongoDB
            report_id = self.mongo_service.create_report(report_doc)
            
            # Create issues in MongoDB
            issue_documents = []
            for issue_data in issues_data:
                issue_doc = PIDIssueDocument(
                    report_id=report_id,
                    serial_number=issue_data.get('serial_number', 0),
                    pid_reference=issue_data.get('pid_reference', ''),
                    issue_observed=issue_data.get('issue_observed', ''),
                    action_required=issue_data.get('action_required', ''),
                    severity=issue_data.get('severity', IssueSeverity.OBSERVATION.value),
                    category=issue_data.get('category', ''),
                    status=issue_data.get('status', IssueStatus.PENDING.value),
                    approval=issue_data.get('approval', 'Pending'),
                    remark=issue_data.get('remark', 'Pending'),
                    location_x=issue_data.get('location_x'),
                    location_y=issue_data.get('location_y'),
                    page_number=issue_data.get('page_number'),
                )
                issue_documents.append(issue_doc)
            
            # Save issues to MongoDB
            if issue_documents:
                self.mongo_service.create_issues(issue_documents)
            
            logger.info(f"✅ Created report in MongoDB: {report_id} with {len(issue_documents)} issues")
            
            return report_id
            
        except Exception as e:
            logger.error(f"❌ Failed to create hybrid report: {str(e)}")
            raise
    
    def get_report_for_drawing(self, drawing_id: int) -> Dict[str, Any]:
        """
        Get full report data from MongoDB by drawing ID
        
        Args:
            drawing_id: PostgreSQL drawing ID
        
        Returns:
            Dictionary with report and issues
        """
        try:
            # Get report from MongoDB
            report = self.mongo_service.get_report_by_drawing_id(drawing_id)
            
            if not report:
                return None
            
            # Get issues from MongoDB
            issues = self.mongo_service.get_report_issues(str(report._id))
            
            # Convert to dictionary format
            return {
                'report_id': str(report._id),
                'drawing_id': report.drawing_id,
                'drawing_number': report.drawing_number,
                'drawing_title': report.drawing_title,
                'revision': report.revision,
                'project_name': report.project_name,
                'total_issues': report.total_issues,
                'approved_count': report.approved_count,
                'ignored_count': report.ignored_count,
                'pending_count': report.pending_count,
                'critical_count': report.critical_count,
                'major_count': report.major_count,
                'minor_count': report.minor_count,
                'observation_count': report.observation_count,
                'report_data': report.report_data,
                'issues': [self._issue_to_dict(issue) for issue in issues],
                'generated_at': report.generated_at.isoformat(),
                'updated_at': report.updated_at.isoformat(),
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get report: {str(e)}")
            return None
    
    def update_issue_status(
        self,
        report_id: str,
        issue_id: str,
        status: str,
        approval: str = '',
        remark: str = ''
    ) -> bool:
        """
        Update issue status and recalculate report summary
        
        Args:
            report_id: MongoDB report ID
            issue_id: MongoDB issue ID
            status: New status
            approval: Approval status
            remark: Remark text
        
        Returns:
            True if successful
        """
        try:
            # Update issue
            success = self.mongo_service.update_issue_status(
                issue_id,
                status,
                approval,
                remark
            )
            
            if success:
                # Recalculate report summary
                self.mongo_service.update_report_summary(report_id)
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Failed to update issue: {str(e)}")
            return False
    
    def get_user_reports(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all reports for a user from MongoDB"""
        try:
            reports = self.mongo_service.get_user_reports(user_id, limit)
            
            return [{
                'report_id': str(r._id),
                'drawing_id': r.drawing_id,
                'drawing_number': r.drawing_number,
                'drawing_title': r.drawing_title,
                'total_issues': r.total_issues,
                'pending_count': r.pending_count,
                'generated_at': r.generated_at.isoformat(),
            } for r in reports]
            
        except Exception as e:
            logger.error(f"❌ Failed to get user reports: {str(e)}")
            return []
    
    def delete_report(self, report_id: str) -> bool:
        """Delete report and all issues from MongoDB"""
        return self.mongo_service.delete_report(report_id)
    
    def _issue_to_dict(self, issue: PIDIssueDocument) -> Dict[str, Any]:
        """Convert issue document to dictionary"""
        return {
            'issue_id': str(issue._id) if issue._id else None,
            'serial_number': issue.serial_number,
            'pid_reference': issue.pid_reference,
            'issue_observed': issue.issue_observed,
            'action_required': issue.action_required,
            'severity': issue.severity,
            'category': issue.category,
            'status': issue.status,
            'approval': issue.approval,
            'remark': issue.remark,
            'location_x': issue.location_x,
            'location_y': issue.location_y,
            'page_number': issue.page_number,
            'created_at': issue.created_at.isoformat(),
            'updated_at': issue.updated_at.isoformat(),
        }
    
    def generate_report_exports(self, report_id: str) -> Dict[str, str]:
        """
        Generate PDF and Excel exports for a report
        Returns URLs to the generated files
        """
        try:
            # Get report from MongoDB
            report = self.mongo_service.get_report_by_id(report_id)
            if not report:
                return {}
            
            # Get issues
            issues = self.mongo_service.get_report_issues(report_id)
            
            # TODO: Implement PDF/Excel generation
            # This would use the existing report generation services
            # and upload files to S3 or local storage
            
            logger.info(f"Report exports generated for {report_id}")
            
            return {
                'pdf_url': report.pdf_report_url or '',
                'excel_url': report.excel_report_url or '',
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to generate exports: {str(e)}")
            return {}
