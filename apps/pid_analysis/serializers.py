from rest_framework import serializers
from .models import PIDDrawing, PIDAnalysisReport, PIDIssue, ReferenceDocument


class PIDIssueSerializer(serializers.ModelSerializer):
    """Serializer for P&ID issues"""
    
    # Add location_on_drawing from report_data if available
    location_on_drawing = serializers.SerializerMethodField()
    engineering_impact = serializers.SerializerMethodField()
    standard_reference = serializers.SerializerMethodField()
    related_issues = serializers.SerializerMethodField()
    
    class Meta:
        model = PIDIssue
        fields = [
            'id', 'serial_number', 'pid_reference', 'issue_observed',
            'action_required', 'severity', 'category', 'status',
            'approval', 'remark', 'location_on_drawing', 'engineering_impact',
            'standard_reference', 'related_issues', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_location_on_drawing(self, obj):
        """Extract location_on_drawing from analysis report JSON if available"""
        # Try to get report from context first
        report = self.context.get('report')
        if not report and hasattr(obj, 'analysis_report'):
            report = obj.analysis_report
        
        if report and hasattr(report, 'report_data'):
            report_data = report.report_data
            if isinstance(report_data, dict) and 'issues' in report_data:
                # Find matching issue by serial number
                for issue in report_data.get('issues', []):
                    if issue.get('serial_number') == obj.serial_number:
                        return issue.get('location_on_drawing', None)
        return None
    
    def get_engineering_impact(self, obj):
        """Extract engineering_impact from analysis report JSON if available"""
        report = self.context.get('report')
        if not report and hasattr(obj, 'analysis_report'):
            report = obj.analysis_report
            
        if report and hasattr(report, 'report_data'):
            report_data = report.report_data
            if isinstance(report_data, dict) and 'issues' in report_data:
                for issue in report_data.get('issues', []):
                    if issue.get('serial_number') == obj.serial_number:
                        return issue.get('engineering_impact', '')
        return ''
    
    def get_standard_reference(self, obj):
        """Extract standard_reference from analysis report JSON if available"""
        report = self.context.get('report')
        if not report and hasattr(obj, 'analysis_report'):
            report = obj.analysis_report
            
        if report and hasattr(report, 'report_data'):
            report_data = report.report_data
            if isinstance(report_data, dict) and 'issues' in report_data:
                for issue in report_data.get('issues', []):
                    if issue.get('serial_number') == obj.serial_number:
                        return issue.get('standard_reference', '')
        return ''
    
    def get_related_issues(self, obj):
        """Extract related_issues from analysis report JSON if available"""
        report = self.context.get('report')
        if not report and hasattr(obj, 'analysis_report'):
            report = obj.analysis_report
            
        if report and hasattr(report, 'report_data'):
            report_data = report.report_data
            if isinstance(report_data, dict) and 'issues' in report_data:
                for issue in report_data.get('issues', []):
                    if issue.get('serial_number') == obj.serial_number:
                        return issue.get('related_issues', [])
        return []


class PIDAnalysisReportSerializer(serializers.ModelSerializer):
    """Serializer for P&ID analysis reports"""
    
    issues = serializers.SerializerMethodField()
    pid_drawing_number = serializers.CharField(source='pid_drawing.drawing_number', read_only=True)
    
    # Additional fields from report_data JSON
    equipment_datasheets = serializers.SerializerMethodField()
    instrument_schedule = serializers.SerializerMethodField()
    line_list = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    holds_and_notes = serializers.SerializerMethodField()
    specification_breaks = serializers.SerializerMethodField()
    
    class Meta:
        model = PIDAnalysisReport
        fields = [
            'id', 'pid_drawing', 'pid_drawing_number', 'total_issues',
            'approved_count', 'ignored_count', 'pending_count',
            'report_data', 'pdf_report', 'excel_report',
            'issues', 'equipment_datasheets', 'instrument_schedule', 
            'line_list', 'summary', 'holds_and_notes', 'specification_breaks',
            'generated_at', 'updated_at'
        ]
        read_only_fields = ['id', 'generated_at', 'updated_at']
    
    def get_issues(self, obj):
        """Get issues with comprehensive fallback and debugging"""
        import logging
        logger = logging.getLogger(__name__)
        
        # Debug: Log what we're working with
        logger.debug(f"[get_issues] Processing report ID {obj.id}")
        logger.debug(f"[get_issues] report_data type: {type(obj.report_data)}")
        
        # Method 1: Try report_data JSON (most complete data)
        if isinstance(obj.report_data, dict):
            issues_data = obj.report_data.get('issues')
            if issues_data is not None:  # Check for None, allow empty list []
                logger.debug(f"[get_issues] Found {len(issues_data)} issues in report_data")
                if len(issues_data) > 0:
                    logger.debug(f"[get_issues] First issue sample: {list(issues_data[0].keys()) if issues_data else 'N/A'}")
                return issues_data
            else:
                logger.debug(f"[get_issues] No 'issues' key in report_data. Keys: {list(obj.report_data.keys())}")
        
        # Method 2: Try database PIDIssue objects
        db_issues = obj.issues.all()
        if db_issues.exists():
            issue_count = db_issues.count()
            logger.debug(f"[get_issues] Found {issue_count} issues in database")
            return PIDIssueSerializer(db_issues, many=True, context={'report': obj}).data
        
        # Method 3: Check if analysis is still in progress
        if hasattr(obj, 'pid_drawing') and obj.pid_drawing.status == 'processing':
            logger.debug(f"[get_issues] Drawing status is 'processing', analysis may be incomplete")
        
        logger.warning(f"[get_issues] No issues found for report {obj.id} via any method")
        return []
    
    def get_equipment_datasheets(self, obj):
        """Extract equipment datasheets from report_data"""
        if isinstance(obj.report_data, dict):
            return obj.report_data.get('equipment_datasheets', [])
        return []
    
    def get_instrument_schedule(self, obj):
        """Extract instrument schedule from report_data"""
        if isinstance(obj.report_data, dict):
            return obj.report_data.get('instrument_schedule', [])
        return []
    
    def get_line_list(self, obj):
        """Extract line list from report_data"""
        if isinstance(obj.report_data, dict):
            return obj.report_data.get('line_list', [])
        return []
    
    def get_summary(self, obj):
        """Extract summary from report_data"""
        if isinstance(obj.report_data, dict):
            return obj.report_data.get('summary', {})
        return {}
    
    def get_holds_and_notes(self, obj):
        """Extract HOLDS and NOTES compliance from report_data"""
        if isinstance(obj.report_data, dict):
            pfd_compliance = obj.report_data.get('pfd_guidelines_compliance', {})
            return pfd_compliance.get('holds_and_notes_compliance', {})
        return {}
    
    def get_specification_breaks(self, obj):
        """Extract specification breaks from report_data"""
        if isinstance(obj.report_data, dict):
            return obj.report_data.get('specification_breaks', [])
        return []


class PIDDrawingSerializer(serializers.ModelSerializer):
    """Serializer for P&ID drawings"""
    
    analysis_report = PIDAnalysisReportSerializer(read_only=True)
    uploaded_by_username = serializers.CharField(source='uploaded_by.username', read_only=True)
    
    class Meta:
        model = PIDDrawing
        fields = [
            'id', 'file', 'original_filename', 'file_size',
            'drawing_number', 'drawing_title', 'revision', 'project_name',
            'status', 'analysis_started_at', 'analysis_completed_at',
            'uploaded_by', 'uploaded_by_username', 'analysis_report',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'file_size', 'uploaded_by', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        """Create PIDDrawing with file metadata"""
        file = validated_data['file']
        validated_data['original_filename'] = file.name
        validated_data['file_size'] = file.size
        return super().create(validated_data)


class PIDDrawingUploadSerializer(serializers.Serializer):
    """Serializer for P&ID drawing upload"""
    
    file = serializers.FileField(
        help_text='P&ID drawing in PDF format',
        required=True
    )
    drawing_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    drawing_title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    revision = serializers.CharField(max_length=20, required=False, allow_blank=True)
    project_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    auto_analyze = serializers.BooleanField(default=True, help_text='Automatically start analysis after upload')
    
    def to_internal_value(self, data):
        """Override to handle FormData boolean strings"""
        # Convert string booleans to actual booleans (FormData sends 'true'/'false' as strings)
        if 'auto_analyze' in data:
            value = data.get('auto_analyze')
            if isinstance(value, str):
                data = data.copy() if hasattr(data, 'copy') else dict(data)
                data['auto_analyze'] = value.lower() in ('true', '1', 'yes')
        
        return super().to_internal_value(data)
    
    def validate_file(self, value):
        """Validate PDF file"""
        if not value:
            raise serializers.ValidationError("File is required")
        
        # Get filename
        filename = getattr(value, 'name', '')
        if not filename:
            raise serializers.ValidationError("File must have a name")
        
        # Check file extension
        if not filename.lower().endswith('.pdf'):
            raise serializers.ValidationError(
                f"Only PDF files are allowed. Received: {filename}"
            )
        
        # Check file size (max 50MB)
        max_size = 50 * 1024 * 1024  # 50MB
        file_size = getattr(value, 'size', 0)
        if file_size > max_size:
            raise serializers.ValidationError(
                f"File size cannot exceed 50MB. Current size: {file_size / (1024*1024):.2f}MB"
            )
        
        if file_size == 0:
            raise serializers.ValidationError("File cannot be empty")
        
        return value


class IssueUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating issue status, severity, and other fields"""
    
    class Meta:
        model = PIDIssue
        fields = ['severity', 'status', 'approval', 'remark']
        
    def validate_severity(self, value):
        """Validate severity choices"""
        valid_choices = ['critical', 'major', 'minor', 'observation']
        if value not in valid_choices:
            raise serializers.ValidationError(f"Invalid severity. Must be one of: {', '.join(valid_choices)}")
        return value
    
    def validate_status(self, value):
        """Validate status choices"""
        valid_choices = ['pending', 'approved', 'ignored']
        if value not in valid_choices:
            raise serializers.ValidationError(f"Invalid status. Must be one of: {', '.join(valid_choices)}")
        return value


class ReferenceDocumentSerializer(serializers.ModelSerializer):
    """Serializer for reference documents used in RAG"""
    
    uploaded_by_username = serializers.CharField(source='uploaded_by.username', read_only=True)
    file_url = serializers.SerializerMethodField()
    
    class Meta:
        model = ReferenceDocument
        fields = [
            'id', 'title', 'description', 'category', 'file', 'file_url',
            'content_text', 'chunk_count', 'vector_db_ids', 'embedding_status',
            'is_active', 'uploaded_by', 'uploaded_by_username',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'content_text', 'chunk_count', 'vector_db_ids',
            'embedding_status', 'uploaded_by', 'created_at', 'updated_at'
        ]
    
    def get_file_url(self, obj):
        """Get the file URL"""
        if obj.file:
            request = self.context.get('request')
            if request is not None:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None


class ReferenceDocumentUploadSerializer(serializers.Serializer):
    """Serializer for uploading reference documents"""
    
    file = serializers.FileField(
        help_text='Reference document in PDF, DOCX, or TXT format',
        required=True
    )
    title = serializers.CharField(max_length=255, required=True)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.ChoiceField(
        choices=['standard', 'guideline', 'specification', 'procedure', 'other'],
        default='other'
    )
    
    def validate_file(self, value):
        """Validate document file"""
        if not value:
            raise serializers.ValidationError("File is required")
        
        filename = getattr(value, 'name', '')
        if not filename:
            raise serializers.ValidationError("File must have a name")
        
        # Check file extension
        allowed_extensions = ['.pdf', '.docx', '.txt']
        if not any(filename.lower().endswith(ext) for ext in allowed_extensions):
            raise serializers.ValidationError(
                f"Only PDF, DOCX, and TXT files are allowed. Received: {filename}"
            )
        
        # Check file size (max 100MB)
        max_size = 100 * 1024 * 1024  # 100MB
        file_size = getattr(value, 'size', 0)
        if file_size > max_size:
            raise serializers.ValidationError(
                f"File size cannot exceed 100MB. Current size: {file_size / (1024*1024):.2f}MB"
            )
        
        if file_size == 0:
            raise serializers.ValidationError("File cannot be empty")
        
        return value
