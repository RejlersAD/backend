"""
PFD Verification Views
API endpoints for PFD verification and analysis
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.utils import timezone
from django.core.files.storage import default_storage
from django.http import HttpResponse
import logging
import io
import xlsxwriter
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from ..models import PFDUpload, PFDVerificationReport, PFDIssue, PFDProject
from ..serializers import (
    PFDVerificationReportSerializer,
    PFDIssueSerializer,
    IssueUpdateSerializer,
)
from ..services.pfd_analysis_service import PFDAnalysisService

# Fix serializer reference
IssueSerializer = PFDIssueSerializer

logger = logging.getLogger(__name__)


class PFDVerificationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for PFD Verification
    
    Endpoints:
    - POST /api/v1/pfd/verify/ - Start PFD verification
    - GET /api/v1/pfd/verify/{upload_id}/results/ - Get verification results
    - POST /api/v1/pfd/verify/{upload_id}/update-issues/ - Update issue status
    - GET /api/v1/pfd/verify/{upload_id}/report/ - Get formatted report
    """
    permission_classes = [IsAuthenticated]
    serializer_class = PFDVerificationReportSerializer
    lookup_field = 'pfd_upload__upload_id'
    lookup_url_kwarg = 'upload_id'
    
    def get_queryset(self):
        """Get verification reports for current user's uploads"""
        user = self.request.user
        if hasattr(user, 'user'):
            user = user.user
        
        return PFDVerificationReport.objects.filter(
            pfd_upload__uploaded_by=user
        ).select_related('pfd_upload', 'pfd_upload__project')
    
    @action(detail=False, methods=['post'], url_path='start-verification')
    def start_verification(self, request):
        """
        Start PFD verification process
        
        POST /api/v1/pfd/verify/start-verification/
        Body:
        {
            "upload_id": "PFDU-20260206-XXXX",
            "auto_analyze": true
        }
        """
        try:
            upload_id = request.data.get('upload_id')
            auto_analyze = request.data.get('auto_analyze', True)
            
            if not upload_id:
                return Response({
                    'success': False,
                    'error': 'upload_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get PFD upload
            try:
                pfd_upload = PFDUpload.objects.get(
                    upload_id=upload_id,
                    uploaded_by=request.user
                )
            except PFDUpload.DoesNotExist:
                return Response({
                    'success': False,
                    'error': f'PFD upload {upload_id} not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            if not auto_analyze:
                return Response({
                    'success': True,
                    'message': 'PFD uploaded successfully. Start verification manually.',
                    'upload_id': pfd_upload.upload_id
                })
            
            # Update status to processing
            pfd_upload.status = 'processing'
            pfd_upload.processed_at = timezone.now()
            pfd_upload.save()
            
            # Get PFD file
            pfd_file = default_storage.open(pfd_upload.file_path, 'rb')
            
            # Get reference documents from project
            reference_docs = {}
            if pfd_upload.project and pfd_upload.project.reference_documents:
                for doc_type, doc_path in pfd_upload.project.reference_documents.items():
                    if doc_path and doc_path != 'null':
                        reference_docs[doc_type] = doc_path
            
            # Prepare drawing metadata
            drawing_metadata = {
                'drawing_number': pfd_upload.drawing_number,
                'revision': pfd_upload.drawing_revision,
                'title': pfd_upload.drawing_title,
                'project_name': pfd_upload.project_name_field,
            }
            
            logger.info(f"Starting PFD verification for {upload_id}")
            logger.info(f"Reference documents: {len(reference_docs)}")
            
            # Perform AI analysis
            analysis_service = PFDAnalysisService()
            analysis_result = analysis_service.analyze_pfd_document(
                pfd_file=pfd_file,
                reference_documents=reference_docs,
                drawing_metadata=drawing_metadata
            )
            
            pfd_file.close()
            
            # Create or update verification report
            report, created = PFDVerificationReport.objects.update_or_create(
                pfd_upload=pfd_upload,
                defaults={
                    'report_data': analysis_result,
                    'total_issues': analysis_result.get('summary', {}).get('total_issues', 0),
                    'critical_count': analysis_result.get('summary', {}).get('critical_count', 0),
                    'major_count': analysis_result.get('summary', {}).get('major_count', 0),
                    'minor_count': analysis_result.get('summary', {}).get('minor_count', 0),
                    'observation_count': analysis_result.get('summary', {}).get('observation_count', 0),
                    'pending_count': analysis_result.get('summary', {}).get('total_issues', 0),
                    'extracted_drawing_number': analysis_result.get('drawing_info', {}).get('drawing_number', ''),
                    'extracted_revision': analysis_result.get('drawing_info', {}).get('revision', ''),
                    'extracted_project_name': analysis_result.get('drawing_info', {}).get('project_name', ''),
                    'extracted_client_name': analysis_result.get('drawing_info', {}).get('client_name', ''),
                }
            )
            
            # Delete existing issues if recreating
            if not created:
                report.issues.all().delete()
            
            # Create issues
            issues_created = 0
            for issue_data in analysis_result.get('issues', []):
                PFDIssue.objects.create(
                    report=report,
                    serial_number=issue_data.get('serial_number', issues_created + 1),
                    issue_found=issue_data.get('issue_found', ''),
                    action_required=issue_data.get('action_required', ''),
                    severity=issue_data.get('severity', 'observation'),
                    category=issue_data.get('category', 'Other'),
                    status='pending',
                    approval=issue_data.get('approval', 'Pending'),
                    remark=issue_data.get('remark', 'Pending'),
                )
                issues_created += 1
            
            # Update PFD upload status
            pfd_upload.status = 'completed'
            pfd_upload.verification_results = analysis_result
            pfd_upload.save()
            
            logger.info(f"PFD verification completed for {upload_id}")
            logger.info(f"Created {issues_created} issues")
            
            serializer = PFDVerificationReportSerializer(report)
            return Response({
                'success': True,
                'message': f'PFD verification completed. Found {issues_created} issues.',
                'report': serializer.data
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error in PFD verification: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            # Update PFD upload status to failed
            if 'pfd_upload' in locals():
                pfd_upload.status = 'failed'
                pfd_upload.save()
            
            return Response({
                'success': False,
                'error': f'Verification failed: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'], url_path='results')
    def get_results(self, request, upload_id=None):
        """
        Get verification results for a PFD upload
        
        GET /api/v1/pfd/verify/{upload_id}/results/
        """
        try:
            report = self.get_object()
            serializer = self.get_serializer(report)
            
            return Response({
                'success': True,
                'report': serializer.data
            })
        except PFDVerificationReport.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Verification report not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error getting verification results: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'], url_path='update-issues')
    def update_issues(self, request, upload_id=None):
        """
        Bulk update issue status (approve/ignore)
        
        POST /api/v1/pfd/verify/{upload_id}/update-issues/
        Body:
        {
            "issue_ids": [1, 2, 3],
            "status": "approved",
            "approval": "Approved",
            "remark": "Issue verified and accepted"
        }
        """
        try:
            report = self.get_object()
            serializer = IssueUpdateSerializer(data=request.data)
            
            if not serializer.is_valid():
                return Response({
                    'success': False,
                    'errors': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)
            
            issue_ids = serializer.validated_data['issue_ids']
            update_data = {}
            
            if 'status' in serializer.validated_data:
                update_data['status'] = serializer.validated_data['status']
            if 'approval' in serializer.validated_data:
                update_data['approval'] = serializer.validated_data['approval']
            if 'remark' in serializer.validated_data:
                update_data['remark'] = serializer.validated_data['remark']
            
            # Update issues
            updated_count = PFDIssue.objects.filter(
                report=report,
                id__in=issue_ids
            ).update(**update_data)
            
            # Recalculate report summary
            report.approved_count = report.issues.filter(status='approved').count()
            report.ignored_count = report.issues.filter(status='ignored').count()
            report.pending_count = report.issues.filter(status='pending').count()
            report.save()
            
            logger.info(f"Updated {updated_count} issues for report {report.id}")
            
            return Response({
                'success': True,
                'message': f'Updated {updated_count} issues',
                'updated_count': updated_count,
                'report_summary': {
                    'approved_count': report.approved_count,
                    'ignored_count': report.ignored_count,
                    'pending_count': report.pending_count
                }
            })
            
        except Exception as e:
            logger.error(f"Error updating issues: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'], url_path='report')
    def get_formatted_report(self, request, upload_id=None):
        """
        Get formatted report data for frontend display
        
        GET /api/v1/pfd/verify/{upload_id}/report/
        """
        try:
            report = self.get_object()
            issues = report.issues.all()
            
            # Format issues by category
            issues_by_category = {}
            for issue in issues:
                category = issue.category or 'Other'
                if category not in issues_by_category:
                    issues_by_category[category] = []
                issues_by_category[category].append({
                    'id': issue.id,
                    'serial_number': issue.serial_number,
                    'issue_found': issue.issue_found,
                    'action_required': issue.action_required,
                    'severity': issue.severity,
                    'status': issue.status,
                    'approval': issue.approval,
                    'remark': issue.remark,
                })
            
            return Response({
                'success': True,
                'report': {
                    'upload_id': report.pfd_upload.upload_id,
                    'drawing_info': {
                        'drawing_number': report.extracted_drawing_number or report.pfd_upload.drawing_number,
                        'revision': report.extracted_revision or report.pfd_upload.drawing_revision,
                        'title': report.pfd_upload.drawing_title,
                        'project_name': report.extracted_project_name or report.pfd_upload.project_name_field,
                        'client_name': report.extracted_client_name,
                    },
                    'summary': {
                        'total_issues': report.total_issues,
                        'critical_count': report.critical_count,
                        'major_count': report.major_count,
                        'minor_count': report.minor_count,
                        'observation_count': report.observation_count,
                        'approved_count': report.approved_count,
                        'ignored_count': report.ignored_count,
                        'pending_count': report.pending_count,
                    },
                    'issues_by_category': issues_by_category,
                    'all_issues': IssueSerializer(issues, many=True).data,
                    'generated_at': report.generated_at,
                }
            })
        
        except PFDVerificationReport.DoesNotExist:
            return Response(
                {'error': 'Verification report not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error getting formatted report: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'], url_path='export-excel')
    def export_excel(self, request, upload_id=None):
        """
        Export verification report to Excel
        GET /api/v1/pfd/verify/{upload_id}/export-excel/
        """
        try:
            user = request.user
            if hasattr(user, 'user'):
                user = user.user
            
            # Get report
            report = PFDVerificationReport.objects.select_related('pfd_upload').get(
                pfd_upload__upload_id=upload_id,
                pfd_upload__uploaded_by=user
            )
            
            # Get all issues
            issues = PFDIssue.objects.filter(report=report).order_by('serial_number')
            
            # Create Excel file in memory
            output = io.BytesIO()
            workbook = xlsxwriter.Workbook(output, {'in_memory': True})
            
            # Add formats
            header_format = workbook.add_format({
                'bold': True,
                'bg_color': '#4472C4',
                'font_color': 'white',
                'border': 1,
                'align': 'center',
                'valign': 'vcenter'
            })
            
            cell_format = workbook.add_format({
                'border': 1,
                'text_wrap': True,
                'valign': 'top'
            })
            
            critical_format = workbook.add_format({
                'border': 1,
                'bg_color': '#FF0000',
                'font_color': 'white',
                'bold': True,
                'align': 'center'
            })
            
            major_format = workbook.add_format({
                'border': 1,
                'bg_color': '#FFA500',
                'font_color': 'white',
                'bold': True,
                'align': 'center'
            })
            
            minor_format = workbook.add_format({
                'border': 1,
                'bg_color': '#FFFF00',
                'bold': True,
                'align': 'center'
            })
            
            observation_format = workbook.add_format({
                'border': 1,
                'bg_color': '#ADD8E6',
                'align': 'center'
            })
            
            # Create summary worksheet
            summary_sheet = workbook.add_worksheet('Summary')
            summary_sheet.set_column('A:A', 30)
            summary_sheet.set_column('B:B', 20)
            
            # Write summary
            summary_sheet.write('A1', 'PFD VERIFICATION REPORT', header_format)
            summary_sheet.merge_range('A1:B1', 'PFD VERIFICATION REPORT', header_format)
            
            row = 2
            summary_sheet.write(row, 0, 'Drawing Number:', header_format)
            summary_sheet.write(row, 1, report.extracted_drawing_number or report.pfd_upload.drawing_number, cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Revision:', header_format)
            summary_sheet.write(row, 1, report.extracted_revision or report.pfd_upload.drawing_revision, cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Drawing Title:', header_format)
            summary_sheet.write(row, 1, report.pfd_upload.drawing_title, cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Project Name:', header_format)
            summary_sheet.write(row, 1, report.extracted_project_name or '', cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Generated Date:', header_format)
            summary_sheet.write(row, 1, report.generated_at.strftime('%Y-%m-%d %H:%M:%S'), cell_format)
            row += 2
            
            # Issue summary
            summary_sheet.write(row, 0, 'Total Issues:', header_format)
            summary_sheet.write(row, 1, report.total_issues, cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Critical:', critical_format)
            summary_sheet.write(row, 1, report.critical_count, cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Major:', major_format)
            summary_sheet.write(row, 1, report.major_count, cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Minor:', minor_format)
            summary_sheet.write(row, 1, report.minor_count, cell_format)
            row += 1
            
            summary_sheet.write(row, 0, 'Observations:', observation_format)
            summary_sheet.write(row, 1, report.observation_count, cell_format)
            
            # Create issues worksheet
            issues_sheet = workbook.add_worksheet('Issues')
            issues_sheet.set_column('A:A', 8)  # S/N
            issues_sheet.set_column('B:B', 15)  # Severity
            issues_sheet.set_column('C:C', 20)  # Category
            issues_sheet.set_column('D:D', 40)  # Issue Found
            issues_sheet.set_column('E:E', 40)  # Action Required
            issues_sheet.set_column('F:F', 15)  # Status
            issues_sheet.set_column('G:G', 15)  # Approval
            issues_sheet.set_column('H:H', 30)  # Remark
            
            # Write headers
            headers = ['S/N', 'Severity', 'Category', 'Issue Found', 'Action Required', 'Status', 'Approval', 'Remark']
            for col, header in enumerate(headers):
                issues_sheet.write(0, col, header, header_format)
            
            # Write issues
            for row, issue in enumerate(issues, start=1):
                issues_sheet.write(row, 0, issue.serial_number, cell_format)
                
                # Severity with color
                severity_fmt = cell_format
                if issue.severity == 'critical':
                    severity_fmt = critical_format
                elif issue.severity == 'major':
                    severity_fmt = major_format
                elif issue.severity == 'minor':
                    severity_fmt = minor_format
                elif issue.severity == 'observation':
                    severity_fmt = observation_format
                    
                issues_sheet.write(row, 1, issue.severity.upper(), severity_fmt)
                issues_sheet.write(row, 2, issue.category, cell_format)
                issues_sheet.write(row, 3, issue.issue_found, cell_format)
                issues_sheet.write(row, 4, issue.action_required, cell_format)
                issues_sheet.write(row, 5, issue.status.upper(), cell_format)
                issues_sheet.write(row, 6, issue.approval or '', cell_format)
                issues_sheet.write(row, 7, issue.remark or '', cell_format)
            
            workbook.close()
            output.seek(0)
            
            # Create response
            response = HttpResponse(
                output.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            filename = f"PFD_Verification_{report.pfd_upload.drawing_number}_{timezone.now().strftime('%Y%m%d')}.xlsx"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            return response
            
        except PFDVerificationReport.DoesNotExist:
            return Response(
                {'error': 'Verification report not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error exporting to Excel: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'], url_path='export-pdf')
    def export_pdf(self, request, upload_id=None):
        """
        Export verification report to PDF
        GET /api/v1/pfd/verify/{upload_id}/export-pdf/
        """
        try:
            user = request.user
            if hasattr(user, 'user'):
                user = user.user
            
            # Get report
            report = PFDVerificationReport.objects.select_related('pfd_upload').get(
                pfd_upload__upload_id=upload_id,
                pfd_upload__uploaded_by=user
            )
            
            # Get all issues
            issues = PFDIssue.objects.filter(report=report).order_by('serial_number')
            
            # Create PDF in memory
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
            
            # Container for PDF elements
            elements = []
            
            # Styles
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=18,
                textColor=colors.HexColor('#1f4788'),
                alignment=TA_CENTER,
                spaceAfter=20
            )
            
            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=14,
                textColor=colors.HexColor('#1f4788'),
                spaceAfter=10
            )
            
            # Title
            elements.append(Paragraph("PFD VERIFICATION REPORT", title_style))
            elements.append(Spacer(1, 0.2*inch))
            
            # Drawing Information Table
            drawing_data = [
                ['Drawing Number:', report.extracted_drawing_number or report.pfd_upload.drawing_number],
                ['Revision:', report.extracted_revision or report.pfd_upload.drawing_revision],
                ['Drawing Title:', report.pfd_upload.drawing_title],
                ['Project Name:', report.extracted_project_name or ''],
                ['Client Name:', report.extracted_client_name or ''],
                ['Generated Date:', report.generated_at.strftime('%Y-%m-%d %H:%M:%S')],
            ]
            
            drawing_table = Table(drawing_data, colWidths=[2*inch, 4*inch])
            drawing_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.whitesmoke),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BACKGROUND', (1, 0), (1, -1), colors.white),
            ]))
            
            elements.append(drawing_table)
            elements.append(Spacer(1, 0.3*inch))
            
            # Summary Section
            elements.append(Paragraph("SUMMARY", heading_style))
            summary_data = [
                ['Total Issues', 'Critical', 'Major', 'Minor', 'Observations'],
                [
                    str(report.total_issues),
                    str(report.critical_count),
                    str(report.major_count),
                    str(report.minor_count),
                    str(report.observation_count)
                ]
            ]
            
            summary_table = Table(summary_data, colWidths=[1.2*inch]*5)
            summary_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('BACKGROUND', (1, 1), (1, 1), colors.HexColor('#FF6B6B')),
                ('BACKGROUND', (2, 1), (2, 1), colors.HexColor('#FFA500')),
                ('BACKGROUND', (3, 1), (3, 1), colors.HexColor('#FFEB3B')),
                ('BACKGROUND', (4, 1), (4, 1), colors.HexColor('#ADD8E6')),
            ]))
            
            elements.append(summary_table)
            elements.append(Spacer(1, 0.3*inch))
            
            # Issues Section
            if issues.exists():
                elements.append(Paragraph("DETAILED ISSUES", heading_style))
                
                # Issues table
                issues_data = [['S/N', 'Severity', 'Category', 'Issue Found', 'Action Required', 'Status']]
                
                for issue in issues:
                    issues_data.append([
                        str(issue.serial_number),
                        issue.severity.upper(),
                        issue.category,
                        issue.issue_found[:100] + '...' if len(issue.issue_found) > 100 else issue.issue_found,
                        issue.action_required[:100] + '...' if len(issue.action_required) > 100 else issue.action_required,
                        issue.status.upper()
                    ])
                
                issues_table = Table(issues_data, colWidths=[0.5*inch, 0.8*inch, 1.2*inch, 2*inch, 2*inch, 0.8*inch])
                issues_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F0F0')]),
                ]))
                
                elements.append(issues_table)
            
            # Build PDF
            doc.build(elements)
            buffer.seek(0)
            
            # Create response
            response = HttpResponse(buffer.read(), content_type='application/pdf')
            filename = f"PFD_Verification_{report.pfd_upload.drawing_number}_{timezone.now().strftime('%Y%m%d')}.pdf"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            return response
            
        except PFDVerificationReport.DoesNotExist:
            return Response(
                {'error': 'Verification report not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error exporting to PDF: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
