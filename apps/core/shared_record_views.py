"""Source-scoped shared-record review endpoints within Project Control."""
from django.core.paginator import EmptyPage, Paginator
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .shared_records import adapters, adapter_for, link_record, record_payload, require_access, source_row
from .shared_record_targets import require_target, search_targets


class SharedRecordReadView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'read'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_access(request.user, self.permission_action)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class QueueQuery(serializers.Serializer):
    source_type = serializers.CharField(required=False, default='project_client', max_length=40)
    status = serializers.ChoiceField(choices=['all', 'unlinked', 'linked'], default='unlinked')
    search = serializers.CharField(required=False, default='', allow_blank=True, max_length=160)
    page = serializers.IntegerField(default=1, min_value=1)
    page_size = serializers.IntegerField(default=25, min_value=1, max_value=50)


class SharedRecordQueueView(SharedRecordReadView):
    def get(self, request):
        query = QueueQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        adapter = adapter_for(values['source_type'])
        queryset = adapter.filter_queryset(adapter.queryset(request.user), values['status'], values['search']).order_by('pk')
        paginator = Paginator(queryset, values['page_size'])
        try:
            rows = paginator.page(values['page'])
        except EmptyPage:
            rows = []
        source_modules = {'project_client': 'project_control', 'planning_project': 'planning_package',
                          'schedule_resource': 'planning_package', 'approved_hours': 'project_control',
                          'customer_invoice': 'finance_outgoing', 'receivables_source': 'finance_outgoing'}
        sources = [{'key': key, 'label': item.label} for key, item in adapters().items()
                   if key not in source_modules or module_action_allowed(request.user, source_modules[key], 'read')]
        return Response({'sources': sources, 'count': paginator.count, 'page': values['page'],
                         'page_size': values['page_size'],
                         'results': [record_payload(adapter, row, request.user) for row in rows]})


class SharedRecordDetailView(SharedRecordReadView):
    def get(self, request, source_type, source_id):
        adapter = adapter_for(source_type)
        row = source_row(adapter, source_id, request.user)
        return Response(record_payload(adapter, row, request.user, history=True))


class CandidateQuery(serializers.Serializer):
    kind = serializers.ChoiceField(choices=['client', 'project', 'employee'])
    search = serializers.CharField(required=False, default='', allow_blank=True, max_length=160)
    project_id = serializers.CharField(required=False, max_length=80)


class SharedRecordCandidatesView(SharedRecordReadView):
    def get(self, request, source_type, source_id):
        query = CandidateQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        adapter = adapter_for(source_type)
        row = source_row(adapter, source_id, request.user)
        return Response(adapter.candidates(row, request.user, query.validated_data['kind'], query.validated_data['search']))


class SharedRecordTargetsView(SharedRecordReadView):
    def get(self, request):
        query = CandidateQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        project = require_target(request.user, 'project', values['project_id']) if values.get('project_id') else None
        return Response(search_targets(request.user, values['kind'], values['search'], project))


class LinkInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    expected_token = serializers.CharField(max_length=4000)
    reason = serializers.CharField(max_length=1000, trim_whitespace=True)
    targets = serializers.DictField(child=serializers.CharField(max_length=80), allow_empty=False)

    def validate(self, attrs):
        if set(self.initial_data) - set(self.fields):
            raise ValidationError('Unsupported review fields were supplied.')
        if set(attrs['targets']) - {'client_id', 'project_id', 'employee_id'}:
            raise ValidationError({'targets': 'Unsupported target fields were supplied.'})
        return attrs


class SharedRecordLinkView(SharedRecordReadView):
    permission_action = 'update'

    def post(self, request, source_type, source_id):
        serializer = LinkInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(link_record(source_type, source_id, request.user, **serializer.validated_data))
