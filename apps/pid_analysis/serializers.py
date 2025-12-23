from rest_framework import serializers
from .models import PIDDrawing, PIDAnalysisReport, PIDIssue, ReferenceDocument


class PIDIssueSerializer(serializers.ModelSerializer):
    """Serializer for P&ID issues"""
    
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
    
    # Debug information for troubleshooting
    debug_info = serializers.SerializerMethodField()
    
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
            'issues', 'debug_info', 'equipment_datasheets', 'instrument_schedule', 
            'line_list', 'summary', 'holds_and_notes', 'specification_breaks',
            'generated_at', 'updated_at'
        ]
        read_only_fields = ['id', 'generated_at', 'updated_at']
    
    def get_issues(self, obj):
        """Get issues with comprehensive fallback strategies and debugging"""
        import logging
        logger = logging.getLogger(__name__)
        
        result_issues = []
        debug_info = {
            'report_id': obj.id,
            'total_issues_field': obj.total_issues,
            'methods_tried': []
        }
        
        # PRIORITY METHOD 1: Database PIDIssue objects (have proper IDs for updates)
        db_issues_qs = obj.issues.all().order_by('serial_number')
        if db_issues_qs.exists():
            db_issues_count = db_issues_qs.count()
            result_issues = PIDIssueSerializer(db_issues_qs, many=True, context={'report': obj}).data
            debug_info['methods_tried'].append(f'database PIDIssue objects - found {db_issues_count} issues (WITH IDs)')
            logger.info(f"[get_issues] ✅ Found {db_issues_count} issues WITH IDs in database for report {obj.id}")
        else:
            debug_info['methods_tried'].append('database PIDIssue objects - none found')
        
        # FALLBACK METHOD 2: Extract from report_data JSON (only if no database issues)
        if not result_issues and hasattr(obj, 'report_data') and obj.report_data:
            debug_info['has_report_data'] = True
            debug_info['report_data_type'] = str(type(obj.report_data))
            logger.warning(f"[get_issues] ⚠️ No database issues found, falling back to JSON (issues will lack IDs!)")
            
            if isinstance(obj.report_data, dict):
                debug_info['report_data_keys'] = list(obj.report_data.keys())
                
                # Try multiple possible keys for issues data
                possible_keys = ['issues', 'identified_issues', 'analysis_issues', 'pid_issues']
                for key in possible_keys:
                    if key in obj.report_data:
                        issues_data = obj.report_data[key]
                        if isinstance(issues_data, list):
                            result_issues = issues_data
                            debug_info['methods_tried'].append(f'report_data[{key}] - found {len(issues_data)} issues (NO IDs)')
                            logger.warning(f"[get_issues] Found {len(issues_data)} issues in report_data[{key}] for report {obj.id} - NO DATABASE IDs!")
                            break
                        else:
                            debug_info['methods_tried'].append(f'report_data[{key}] - not a list: {type(issues_data)}')
                
                # Try nested structures
                if not result_issues and 'analysis_result' in obj.report_data:
                    analysis_result = obj.report_data['analysis_result']
                    if isinstance(analysis_result, dict) and 'issues' in analysis_result:
                        nested_issues = analysis_result['issues']
                        if isinstance(nested_issues, list):
                            result_issues = nested_issues
                            debug_info['methods_tried'].append(f'nested analysis_result.issues - found {len(nested_issues)} issues (NO IDs)')
                            logger.warning(f"[get_issues] Found {len(nested_issues)} issues in nested analysis_result for report {obj.id} - NO DATABASE IDs!")
            else:
                debug_info['methods_tried'].append(f'report_data not dict: {type(obj.report_data)}')
        elif not result_issues:
            debug_info['has_report_data'] = False
            debug_info['methods_tried'].append('no report_data available')
        
        # Log debug information
        debug_info['final_count'] = len(result_issues)
        debug_info['has_issues'] = len(result_issues) > 0
        logger.info(f"[get_issues] Final result for report {obj.id}: {debug_info}")
        
        # Add debug info to response for frontend debugging
        if hasattr(self, '_debug_mode') or (hasattr(self.context, 'get') and self.context.get('debug', False)):
            for issue in result_issues:
                if isinstance(issue, dict):
                    issue['_debug_info'] = debug_info
        
        return result_issues
    
    def get_debug_info(self, obj):
        """Provide debug information for troubleshooting"""
        debug_data = {
            'report_id': obj.id,
            'total_issues_field': obj.total_issues,
            'pending_count': obj.pending_count,
            'approved_count': obj.approved_count,
            'ignored_count': obj.ignored_count,
            'has_report_data': bool(obj.report_data),
            'report_data_type': str(type(obj.report_data)),
            'db_issues_count': obj.issues.count(),
            'drawing_status': obj.pid_drawing.status if hasattr(obj, 'pid_drawing') else 'unknown'
        }
        
        if isinstance(obj.report_data, dict):
            debug_data['report_data_keys'] = list(obj.report_data.keys())
            debug_data['has_issues_key'] = 'issues' in obj.report_data
            if 'issues' in obj.report_data:
                issues_data = obj.report_data['issues']
                debug_data['issues_type'] = str(type(issues_data))
                debug_data['issues_length'] = len(issues_data) if isinstance(issues_data, (list, dict)) else 'N/A'
        
        return debug_data
    
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
    
    # Structured Drawing Number Components (Optional - will auto-parse if not provided)
    area = serializers.CharField(max_length=2, required=False, allow_blank=True)
    p_area = serializers.CharField(max_length=2, required=False, allow_blank=True)
    doc_code = serializers.CharField(max_length=2, required=False, allow_blank=True)
    serial_number = serializers.CharField(max_length=4, required=False, allow_blank=True)
    rev = serializers.CharField(max_length=1, required=False, allow_blank=True)
    sheet_number = serializers.CharField(max_length=1, required=False, allow_blank=True, default='1')
    total_sheets = serializers.CharField(max_length=1, required=False, allow_blank=True, default='1')
    
    def validate(self, data):
        """Smart validation and auto-parsing of drawing number"""
        import re
        
        # If structured fields not provided but drawing_number exists, try to parse it
        if data.get('drawing_number') and not data.get('area'):
            drawing_num = data['drawing_number'].strip()
            
            # Pattern: XX-XX-XX-XXXX-X-X/X or XX-XX-XX-XXXX-X or similar
            pattern = r'^(\d{2})-(\d{2})-(\d{2})-(\d{4})(?:-(\d))?(?:-(\d)/(\d))?'
            match = re.match(pattern, drawing_num)
            
            if match:
                data['area'] = match.group(1)
                data['p_area'] = match.group(2)
                data['doc_code'] = match.group(3)
                data['serial_number'] = match.group(4)
                data['rev'] = match.group(5) or ''
                data['sheet_number'] = match.group(6) or '1'
                data['total_sheets'] = match.group(7) or '1'
                print(f"[SMART_PARSE] Auto-parsed drawing number: {drawing_num}")
        
        # If structured fields provided, rebuild drawing_number
        elif all([data.get('area'), data.get('p_area'), data.get('doc_code'), data.get('serial_number')]):
            parts = [data['area'], data['p_area'], data['doc_code'], data['serial_number']]
            if data.get('rev'):
                parts.append(data['rev'])
            if data.get('sheet_number') and data.get('total_sheets'):
                parts.append(f"{data['sheet_number']}/{data['total_sheets']}")
            data['drawing_number'] = '-'.join(parts)
            print(f"[SMART_BUILD] Built drawing number: {data['drawing_number']}")
        
        return data
    
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
        """Validate PDF file with enhanced debugging"""
        print(f"[SERIALIZER] File validation - received value: {value}")
        print(f"[SERIALIZER] Value type: {type(value)}")
        
        if not value:
            print(f"[SERIALIZER] File validation failed: No file provided")
            raise serializers.ValidationError("File is required")
        
        # Check if it's a proper file object
        if not hasattr(value, 'name') or not hasattr(value, 'read'):
            print(f"[SERIALIZER] File validation failed: Not a proper file object")
            raise serializers.ValidationError("The submitted data was not a file. Check the encoding type on the form.")
        
        # Get filename
        filename = getattr(value, 'name', '')
        print(f"[SERIALIZER] File name: {filename}")
        
        if not filename:
            print(f"[SERIALIZER] File validation failed: No filename")
            raise serializers.ValidationError("File must have a name")
        
        # Check file extension (allow PDF, PNG, JPG for broader compatibility)
        allowed_extensions = ['.pdf', '.png', '.jpg', '.jpeg']
        if not any(filename.lower().endswith(ext) for ext in allowed_extensions):
            print(f"[SERIALIZER] File validation failed: Invalid extension for {filename}")
            raise serializers.ValidationError(
                f"Only PDF, PNG, JPG files are allowed. Received: {filename}"
            )
        
        # Check file size (max 50MB)
        max_size = 50 * 1024 * 1024  # 50MB
        file_size = getattr(value, 'size', 0)
        print(f"[SERIALIZER] File size: {file_size} bytes")
        
        if file_size > max_size:
            print(f"[SERIALIZER] File validation failed: File too large")
            raise serializers.ValidationError(
                f"File size cannot exceed 50MB. Current size: {file_size / (1024*1024):.2f}MB"
            )
        
        if file_size == 0:
            print(f"[SERIALIZER] File validation failed: Empty file")
            raise serializers.ValidationError("File cannot be empty")
        
        print(f"[SERIALIZER] File validation successful: {filename} ({file_size} bytes)")
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
