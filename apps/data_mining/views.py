"""
Data Mining API Views
RESTful endpoints for data mining platform
"""
import logging
import json
import pandas as pd
import io
import re
import uuid
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.http import FileResponse
from django.utils import timezone
from django.db import transaction

from apps.wrench_integration import service as wrench_service
from apps.wrench_integration.models import WrenchConfig
from apps.rbac.action_policy import request_action_allowed

from .models import (
    DataMiningProject,
    DataMiningDocument,
    TransformationPipeline,
    TransformationStep,
)
from .serializers import (
    DataMiningProjectSerializer,
    DataMiningProjectCreateSerializer,
    DataMiningDocumentSerializer,
    TransformationPipelineSerializer,
    TransformationStepSerializer,
)
from .transformation_engine import TransformationEngine
from .storage import master_storage

logger = logging.getLogger(__name__)


def valid_master_key(project, key):
    """Never interpret legacy URLs or caller-supplied arbitrary storage paths."""
    prefix = re.escape(f'data-mining/{project.pk}/exports/')
    return isinstance(key, str) and bool(re.fullmatch(prefix + r'[a-f0-9]{32}\.(csv|xlsx|json|parquet)', key))


def table_preview(dataframe, limit):
    """Use pandas JSON normalization for nulls and timestamp-like values."""
    table = json.loads(dataframe.head(limit).to_json(orient='split', date_format='iso'))
    return {'columns': table['columns'], 'rows': table['data']}


def serialize_master(dataframe, file_format):
    """Serialize existing transformed data using the already-supported formats."""
    if file_format == 'csv':
        return dataframe.to_csv(index=False).encode('utf-8'), 'csv'
    if file_format == 'json':
        return dataframe.to_json(orient='records', date_format='iso').encode('utf-8'), 'json'
    output = io.BytesIO()
    if file_format == 'excel':
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False)
            # Extracted strings are evidence, not executable spreadsheet formulas
            # or Excel error cells. Preserve their exact text, including headers.
            for sheet in writer.book.worksheets:
                for row in sheet:
                    for cell in row:
                        if cell.data_type in {'f', 'e'}:
                            cell.data_type = 's'
        return output.getvalue(), 'xlsx'
    if file_format == 'parquet':
        dataframe.to_parquet(output, index=False)
        return output.getvalue(), 'parquet'
    raise ValueError('Unsupported export format.')


class DataMiningProjectViewSet(viewsets.ModelViewSet):
    """
    Data Mining Project management
    
    Endpoints:
        GET    /api/data-mining/projects/          - List all projects
        POST   /api/data-mining/projects/          - Create new project
        GET    /api/data-mining/projects/{id}/     - Get project details
        PATCH  /api/data-mining/projects/{id}/     - Update project
        DELETE /api/data-mining/projects/{id}/     - Delete project
        
        POST   /api/data-mining/projects/{id}/add_documents/     - Add Wrench documents
        POST   /api/data-mining/projects/{id}/extract_data/      - Extract data from documents
        POST   /api/data-mining/projects/{id}/execute_pipeline/  - Run transformation pipeline
        GET    /api/data-mining/projects/{id}/download_master/   - Download master file
    """
    permission_classes = [IsAuthenticated]
    queryset = DataMiningProject.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return DataMiningProjectCreateSerializer
        return DataMiningProjectSerializer
    
    def get_queryset(self):
        # Users can only see their own projects unless they're admin
        user = self.request.user
        if hasattr(user, 'is_admin') and user.is_admin:
            return DataMiningProject.objects.all()
        return DataMiningProject.objects.filter(created_by=user)
    
    @action(detail=True, methods=['post'])
    def add_documents(self, request, pk=None):
        """
        Add Wrench documents to the project
        
        Request body:
            {
                "wrench_documents": [
                    {
                        "doc_number": "DOC-001",
                        "doc_title": "Equipment List",
                        "doc_revision": "A",
                        "transmittal_id": "TR-001"
                    }
                ]
            }
        """
        project = self.get_object()
        wrench_documents = request.data.get('wrench_documents', [])
        
        if not wrench_documents:
            return Response(
                {'error': 'No documents provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        created_docs = []
        with transaction.atomic():
            for idx, doc_data in enumerate(wrench_documents):
                doc = DataMiningDocument.objects.create(
                    project=project,
                    wrench_doc_number=doc_data.get('doc_number', ''),
                    wrench_doc_title=doc_data.get('doc_title', ''),
                    wrench_doc_revision=doc_data.get('doc_revision', ''),
                    wrench_transmittal_id=doc_data.get('transmittal_id', ''),
                    sequence_order=idx,
                )
                created_docs.append(doc)
            
            # Update project
            project.total_documents = project.documents.count()
            project.status = 'configuring'
            project.save()
        
        return Response({
            'message': f'Added {len(created_docs)} documents',
            'documents': DataMiningDocumentSerializer(created_docs, many=True).data
        })
    
    @action(detail=True, methods=['post'])
    def extract_data(self, request, pk=None):
        """No genuine source extractor is connected to this operational route."""
        self.get_object()  # Keep record scope checks before disclosing capability.
        return Response(
            {
                'code': 'extraction_unavailable',
                'error': 'Document extraction is unavailable. Sources, configuration and existing results are unchanged.',
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @action(detail=True, methods=['post'])
    def execute_pipeline(self, request, pk=None):
        """Transform existing extracted inputs; publish only verified artifacts."""
        project = self.get_object()
        # The registered action already requires create. This command also
        # produces an export, so it must not bypass the download/export grant.
        if not request_action_allowed(request, 'data_mining', 'export'):
            raise PermissionDenied('You do not have export permission for this module.')

        failure_stage = 'transformation'
        try:
            with transaction.atomic():
                project = DataMiningProject.objects.select_for_update().get(pk=project.pk)
                try:
                    pipeline = project.pipeline
                except TransformationPipeline.DoesNotExist:
                    return Response(
                        {'code': 'pipeline_missing', 'error': 'No pipeline configured for this project.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                documents = list(project.documents.all())
                if not documents or any(
                    doc.extraction_status != 'completed' or not doc.extracted_data
                    for doc in documents
                ):
                    return Response(
                        {
                            'code': 'extraction_unavailable',
                            'error': 'Every selected source needs existing extracted data. Document extraction is unavailable; sources and configuration are unchanged.',
                        },
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

                start_time = timezone.now()
                dataframes = {}
                for doc in documents:
                    source = doc.extracted_data
                    if not isinstance(source, dict) or not isinstance(source.get('rows'), list) or not isinstance(source.get('columns'), list):
                        raise ValueError('Invalid extracted table.')
                    dataframes[str(doc.pk)] = pd.DataFrame(source['rows'], columns=source['columns'])

                engine = TransformationEngine()
                step_outputs = {}
                step_results = []
                steps = list(pipeline.steps.order_by('sequence_order'))
                for step in steps:
                    if step.input_source:
                        input_df = step_outputs.get(step.input_source)
                        if input_df is None:
                            input_df = dataframes.get(step.input_source)
                        if input_df is None:
                            raise ValueError('Input source unavailable.')
                    else:
                        input_df = next(iter(dataframes.values()))
                    additional_inputs = {}
                    if step.operation_type in ['join', 'union']:
                        additional_inputs.update(step_outputs)
                        additional_inputs.update(dataframes)
                    step_start = timezone.now()
                    output_df = engine.execute(step.operation_type, input_df, step.config, additional_inputs)
                    step_outputs[str(step.pk)] = output_df
                    step_results.append((step, {
                        'status': 'completed',
                        'output_row_count': len(output_df),
                        'output_column_count': len(output_df.columns),
                        'execution_time_ms': (timezone.now() - step_start).total_seconds() * 1000,
                        'output_preview': table_preview(output_df, 100),
                        'error_message': '',
                    }))

                final_df = list(step_outputs.values())[-1] if step_outputs else next(iter(dataframes.values()))
                failure_stage = 'serialization'
                content, extension = serialize_master(final_df, project.master_file_format)
                preview = table_preview(final_df, 20)
                failure_stage = 'storage'
                storage = master_storage()
                run_id = uuid.uuid4().hex
                artifact_key = f'data-mining/{project.pk}/exports/{run_id}.{extension}'
                saved_key = storage.save(artifact_key, ContentFile(content))
                if not valid_master_key(project, saved_key) or not storage.exists(saved_key):
                    raise OSError('Artifact storage did not produce a valid object.')
                with storage.open(saved_key, 'rb') as stored_file:
                    if stored_file.read() != content:
                        raise OSError('Stored artifact does not match the generated output.')

                # Files use unique keys. Retain the preceding result metadata in
                # the existing log before changing the latest-result fields.
                completed_at = timezone.now()
                run_record = {
                    'run_id': run_id,
                    'executed_by': str(request.user.pk),
                    'executed_at': completed_at.isoformat(),
                    'master_file_path': saved_key,
                    'source_documents': [
                        {'id': str(doc.pk), 'updated_at': doc.updated_at.isoformat()}
                        for doc in documents
                    ],
                    'previous_result': {
                        'status': project.status,
                        'master_file_path': project.master_file_path,
                        'total_rows_processed': project.total_rows_processed,
                        'executed_at': project.executed_at.isoformat() if project.executed_at else None,
                        'execution_time_seconds': project.execution_time_seconds,
                        'pipeline_last_executed_at': pipeline.last_executed_at.isoformat() if pipeline.last_executed_at else None,
                        'steps': [
                            {
                                'id': str(step.pk), 'status': step.status,
                                'output_preview': step.output_preview,
                                'output_row_count': step.output_row_count,
                                'output_column_count': step.output_column_count,
                                'execution_time_ms': step.execution_time_ms,
                                'error_message': step.error_message,
                            }
                            for step in steps
                        ],
                    },
                }
                failure_stage = 'persistence'
                for step, values in step_results:
                    for field, value in values.items():
                        setattr(step, field, value)
                    step.save(update_fields=[*values, 'updated_at'])

                project.total_rows_processed = len(final_df)
                project.master_file_path = saved_key
                project.status = 'completed'
                project.executed_at = completed_at
                project.execution_time_seconds = (completed_at - start_time).total_seconds()
                project.save(update_fields=[
                    'total_rows_processed', 'master_file_path', 'status',
                    'executed_at', 'execution_time_seconds', 'updated_at',
                ])
                pipeline.last_executed_at = completed_at
                pipeline.execution_log = (pipeline.execution_log + '\n' if pipeline.execution_log else '') + json.dumps(run_record)
                pipeline.save(update_fields=['last_executed_at', 'execution_log', 'updated_at'])
        except Exception:
            # Do not expose source values, provider errors or filesystem details.
            logger.warning('Data Mining execution failed', extra={
                'project_id': str(project.pk), 'failure_stage': failure_stage,
            })
            storage_failure = failure_stage == 'storage'
            return Response(
                {
                    'code': 'artifact_storage_unavailable' if storage_failure else 'pipeline_execution_failed',
                    'error': (
                        'The export could not be stored and verified. Sources, configuration and previous results are unchanged.'
                        if storage_failure else
                        'The pipeline could not produce an export. Sources, configuration and previous results are unchanged.'
                    ),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE if storage_failure else status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'status': 'completed',
            'artifact_available': True,
            'message': 'Pipeline completed and the export was stored.',
            'rows_processed': project.total_rows_processed,
            'execution_time': project.execution_time_seconds,
            'master_file': project.master_file_path,
            'filename': f'master.{extension}',
            'preview': preview,
        })

    @action(detail=True, methods=['get'])
    def download_master(self, request, pk=None):
        """Stream the actual project-owned artifact after current authorization."""
        project = self.get_object()
        expected_key = request.query_params.get('expected_master_file')
        if expected_key is not None and expected_key != project.master_file_path:
            return Response(
                {'code': 'artifact_changed', 'error': 'The project export changed. Refresh the result before downloading.'},
                status=status.HTTP_409_CONFLICT,
            )
        if not valid_master_key(project, project.master_file_path):
            return Response(
                {'code': 'artifact_unavailable', 'error': 'No verified stored export is available for this project.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            storage = master_storage()
            if not storage.exists(project.master_file_path):
                raise FileNotFoundError
            stored_file = storage.open(project.master_file_path, 'rb')
        except FileNotFoundError:
            return Response(
                {'code': 'artifact_unavailable', 'error': 'The stored export is unavailable. No download was started.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception:
            logger.warning('Data Mining export storage unavailable', extra={'project_id': str(project.pk)})
            return Response(
                {'code': 'artifact_storage_unavailable', 'error': 'Export storage is unavailable. Please try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        extension = project.master_file_path.rsplit('.', 1)[1]
        response = FileResponse(stored_file, as_attachment=True, filename=f'master.{extension}')
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class TransformationPipelineViewSet(viewsets.ModelViewSet):
    """
    Transformation Pipeline management
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TransformationPipelineSerializer
    queryset = TransformationPipeline.objects.all()

    def get_queryset(self):
        # Generated previews/history inherit the existing project owner scope.
        user = self.request.user
        if hasattr(user, 'is_admin') and user.is_admin:
            return TransformationPipeline.objects.all()
        return TransformationPipeline.objects.filter(project__created_by=user)
    
    @action(detail=True, methods=['post'])
    def add_step(self, request, pk=None):
        """
        Add a transformation step to the pipeline
        
        Request body:
            {
                "step_name": "Join with Equipment List",
                "operation_type": "join",
                "config": {
                    "join_type": "inner",
                    "right_input": "doc_id_2",
                    "left_key": "equipment_id",
                    "right_key": "id"
                },
                "input_source": "doc_id_1",
                "sequence_order": 1
            }
        """
        pipeline = self.get_object()
        
        step_data = request.data
        step = TransformationStep.objects.create(
            pipeline=pipeline,
            step_name=step_data.get('step_name', 'Unnamed Step'),
            operation_type=step_data.get('operation_type'),
            config=step_data.get('config', {}),
            input_source=step_data.get('input_source', ''),
            sequence_order=step_data.get('sequence_order', pipeline.steps.count())
        )
        
        return Response(TransformationStepSerializer(step).data)


class WrenchDocumentSearchViewSet(viewsets.ViewSet):
    """
    Wrench document search integration for Data Mining
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def search(self, request):
        """
        Search Wrench documents
        
        Query params:
            project_number: Wrench project/order number
            search_term: Search in document title/number
        """
        try:
            config = WrenchConfig.objects.first()
            if not config:
                return Response(
                    {'error': 'Wrench integration not configured'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            project_number = request.query_params.get('project_number', '')
            search_term = request.query_params.get('search_term', '')
            
            # Use Wrench service to search documents
            results = wrench_service.search_documents(
                config,
                order_no=project_number,
                doc_no=search_term,
                page=1,
                page_size=100
            )
            
            return Response(results)
            
        except Exception as e:
            logger.error(f"Wrench document search failed: {str(e)}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def projects(self, request):
        """
        Get list of Wrench projects for dropdown
        """
        try:
            config = WrenchConfig.objects.first()
            if not config:
                return Response(
                    {'error': 'Wrench integration not configured'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # This would use the wrench_service to get project list
            # Placeholder response
            projects = [
                {'project_number': 'PRJ-001', 'project_name': 'Oil Refinery Expansion'},
                {'project_number': 'PRJ-002', 'project_name': 'Gas Processing Plant'},
            ]
            
            return Response({'projects': projects})
            
        except Exception as e:
            logger.error(f"Wrench project list failed: {str(e)}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
