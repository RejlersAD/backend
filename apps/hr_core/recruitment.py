"""HR-TA-01: HR-managed recruitment register, separate from employee identity."""
from django.db import transaction
from django.db.models import Prefetch
from rest_framework import filters, mixins, serializers, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.exceptions import ValidationError

from .governance import audit, is_hr
from .models import HRAuditEvent, RecruitmentCandidate, RecruitmentOpening, RecruitmentFormSubmission, RecruitmentFormAttachment


class RecruitmentHRPermission(BasePermission):
    message = 'Only authorized HR administrators can access recruitment records.'

    def has_permission(self, request, view):
        return is_hr(request.user)


class RecruitmentPagination(PageNumberPagination):
    page_size = 100


class OpeningSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecruitmentOpening
        fields = ['id', 'title', 'department', 'location', 'source', 'source_id', 'source_url', 'last_seen_at', 'is_open', 'created_at', 'updated_at']
        read_only_fields = ['id', 'source', 'source_id', 'source_url', 'last_seen_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        if self.instance and self.instance.source == 'rejlers_careers' and {'title', 'location', 'is_open'} & set(attrs):
            raise ValidationError('Website positions are maintained by synchronization. Update the source vacancy or create a manual position.')
        return attrs


class CandidateSerializer(serializers.ModelSerializer):
    reference = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    submitted_forms = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    photo = serializers.SerializerMethodField()
    cv_details = serializers.SerializerMethodField()

    class Meta:
        model = RecruitmentCandidate
        fields = ['id', 'reference', 'name', 'email', 'mobile', 'nationality',
                  'position', 'department', 'status', 'education', 'experience',
                  'skills', 'interview_notes', 'interview_outcome', 'created_at', 'updated_at', 'timeline',
                  'submitted_forms', 'documents', 'photo', 'cv_details']
        read_only_fields = ['id', 'reference', 'created_at', 'updated_at', 'timeline']

    def submitted_source(self, obj):
        if not hasattr(obj, '_submitted_source'):
            from .recruitment_projection import submitted_source
            obj._submitted_source = submitted_source(obj)
        return obj._submitted_source

    def get_submitted_forms(self, obj):
        return self.submitted_source(obj)['submitted_forms']

    def get_documents(self, obj):
        return self.submitted_source(obj)['documents']

    def get_photo(self, obj):
        return self.submitted_source(obj)['photo']

    def get_cv_details(self, obj):
        return self.submitted_source(obj)['cv_details']

    def to_representation(self, instance):
        result = super().to_representation(instance)
        for key, value in self.submitted_source(instance)['projection'].items():
            if not result.get(key):
                result[key] = value
        return result

    def get_reference(self, obj):
        return f'CAN-{obj.pk:04d}'

    def get_timeline(self, obj):
        events = HRAuditEvent.objects.filter(object_type='recruitment_candidate', object_id=str(obj.pk)).order_by('-created_at')[:8]
        return [{'label': event.metadata.get('label', 'Candidate updated'), 'date': event.created_at} for event in events]


class RegisterViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                      mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                      viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, RecruitmentHRPermission]
    pagination_class = RecruitmentPagination
    filter_backends = [filters.SearchFilter]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    @transaction.atomic
    def perform_create(self, serializer):
        obj = serializer.save()
        self.record_event(obj, 'created')

    @transaction.atomic
    def perform_update(self, serializer):
        obj = serializer.save()
        self.record_event(obj, 'updated')

    def record_event(self, obj, operation):
        candidate = isinstance(obj, RecruitmentCandidate)
        audit(actor=self.request.user, action=f'recruitment.{operation}',
              object_type='recruitment_candidate' if candidate else 'recruitment_opening',
              object_id=obj.pk, request=self.request,
              metadata={'label': f'Candidate {operation}' if candidate else f'Opening {operation}',
                        **({'status': obj.status, 'interview_outcome': obj.interview_outcome} if candidate else {})})


class RecruitmentCandidateViewSet(RegisterViewSet):
    queryset = RecruitmentCandidate.objects.all().order_by('-created_at', '-pk').prefetch_related(
        'form_invitations', Prefetch('form_invitations__submissions',
            queryset=RecruitmentFormSubmission.objects.order_by('-created_at', '-pk').prefetch_related(
                Prefetch('attachments', queryset=RecruitmentFormAttachment.objects.defer('content')))))
    serializer_class = CandidateSerializer
    search_fields = ['name', 'email', 'position', 'department']


class RecruitmentOpeningViewSet(RegisterViewSet):
    queryset = RecruitmentOpening.objects.all().order_by('title', 'pk')
    serializer_class = OpeningSerializer
    search_fields = ['title', 'department']
