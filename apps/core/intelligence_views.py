"""Project knowledge and saved RADAI Intelligence conversations."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
import difflib
from uuid import UUID, uuid4
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.project_ai_instructions import FIELDS, can_edit, require_read
from apps.core.project_models import (Project, ProjectAIInstructions, IntelligenceConversation,
                                      IntelligenceMessage, ProjectKnowledgeSource)
from apps.project_control.models import ProjectDocument
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import AuditLog

from .intelligence_knowledge import process_source, retrieve, source_available, source_hash_matches


def project_for(user, project_id):
    project = Project.objects.filter(pk=project_id, is_deleted=False).first()
    if not project:
        raise NotFound('Project not found.')
    require_read(user, project)
    return project


def audit(user, action, resource, project, metadata):
    AuditLog.objects.create(user=user, user_email=user.email or '', action=action,
                            resource_type=resource, resource_repr=f'Project {project.code}',
                            metadata={'project_id': project.pk, **metadata})


def source_payload(source):
    if source.crs_revision_id:
        revision = source.crs_revision
        name = revision.document.document_name
        revision_label = revision.revision_label
        approval = ('approved' if revision.chain.status == 'approved' and revision.status == 'completed'
                    and revision.document.status == 'completed' else 'unverified')
        document_id = None
    else:
        document = source.project_document
        name = document.title or document.original_filename
        revision_label = None
        approval = 'unverified'
        document_id = document.pk
    return {'id': source.pk, 'project_id': source.project_id, 'project_document_id': document_id,
            'crs_revision_id': source.crs_revision_id, 'name': name, 'revision': revision_label,
            'approval': approval, 'status': source.status, 'error': source.error,
            'sha256': source.content_sha256, 'locations': [page['location'] for page in source.extracted_pages],
            'created_at': source.created_at.isoformat()}


class IntelligenceKnowledgeView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, project_id):
        project = project_for(request.user, project_id)
        sources = ProjectKnowledgeSource.objects.filter(project=project).filter(
            Q(project_document__is_deleted=False) |
            Q(crs_revision__pid_evidence_links__project=project),
        )
        if not (module_action_allowed(request.user, 'crs_documents', 'read')
                and module_action_allowed(request.user, 'pid_analysis', 'read')):
            sources = sources.filter(project_document__isnull=False)
        term = request.query_params.get('q', '').strip()
        if term:
            sources = sources.filter(Q(project_document__title__icontains=term) |
                                     Q(project_document__original_filename__icontains=term) |
                                     Q(crs_revision__document__document_name__icontains=term))
        try:
            page = max(1, min(int(request.query_params.get('page', 1)), 1000))
        except (TypeError, ValueError):
            raise ValidationError({'page': 'Enter a valid page number.'})
        start = (page - 1) * 20
        rows = list(sources.select_related('project_document', 'crs_revision__document',
                                           'crs_revision__chain').distinct().order_by('-created_at', '-pk')[start:start + 21])
        return Response({'results': [source_payload(source) for source in rows[:20]
                                     if source_available(source, request.user)],
                         'next': page + 1 if len(rows) > 20 else None,
                         'can_edit': can_edit(request.user, project)})

    def post(self, request, project_id):
        project = project_for(request.user, project_id)
        if not can_edit(request.user, project):
            raise PermissionDenied('Project knowledge edit access is required.')
        document_id = request.data.get('project_document_id')
        revision_id = request.data.get('crs_revision_id')
        if bool(document_id) == bool(revision_id):
            raise ValidationError('Provide exactly one existing project document or CRS revision.')
        if document_id:
            document = ProjectDocument.objects.filter(pk=document_id, project=project, is_deleted=False).first()
            if not document:
                raise PermissionDenied('Document is outside this project.')
            lookup = {'project_document': document}
        else:
            from apps.crs.revision_models import CRSRevision
            from apps.pid_checker_v2.models import PidEngineeringEvidenceLink
            if not (module_action_allowed(request.user, 'crs_documents', 'read')
                    and module_action_allowed(request.user, 'pid_analysis', 'read')):
                raise PermissionDenied('CRS and P&ID read access is required.')
            revision = CRSRevision.objects.filter(pk=revision_id).first()
            if not revision or not PidEngineeringEvidenceLink.objects.filter(
                    project=project, revision=revision).exists():
                raise PermissionDenied('CRS revision is not explicitly linked to this project.')
            lookup = {'crs_revision': revision}
        with transaction.atomic():
            source, created = ProjectKnowledgeSource.objects.get_or_create(
                project=project, defaults={'linked_by': request.user}, **lookup,
            )
            if created:
                audit(request.user, 'create', 'ProjectKnowledgeSource', project, {'source_id': source.pk})
        if created:
            with transaction.atomic():
                process_source(source)
                audit(request.user, 'update', 'ProjectKnowledgeSource', project,
                      {'source_id': source.pk, 'processing_status': source.status,
                       'sha256': source.content_sha256})
        source = ProjectKnowledgeSource.objects.select_related(
            'project_document', 'crs_revision__document', 'crs_revision__chain').get(pk=source.pk)
        return Response(source_payload(source), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class IntelligenceKnowledgeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, project_id, source_id):
        project = project_for(request.user, project_id)
        source = ProjectKnowledgeSource.objects.filter(project=project, pk=source_id).select_related(
            'project_document', 'crs_revision__document', 'crs_revision__chain').first()
        if not source:
            raise NotFound('Knowledge source not found.')
        if not source_available(source, request.user):
            raise NotFound('Knowledge source not found.')
        return project, source

    def get(self, request, project_id, source_id):
        _, source = self.get_object(request, project_id, source_id)
        if source.status == 'ready' and not source_hash_matches(source):
            raise KnowledgeSourceChanged()
        return Response({**source_payload(source), 'pages': [
            {'location': page['location'], 'text': page['text'][:4000]} for page in source.extracted_pages
        ]})

    def delete(self, request, project_id, source_id):
        project, source = self.get_object(request, project_id, source_id)
        if not can_edit(request.user, project):
            raise PermissionDenied('Project knowledge edit access is required.')
        with transaction.atomic():
            details = {'source_id': source.pk, 'project_document_id': source.project_document_id,
                       'crs_revision_id': source.crs_revision_id,
                       'sha256': source.content_sha256, 'processing_status': source.status}
            source.delete()
            audit(request.user, 'delete', 'ProjectKnowledgeSource', project, details)
        return Response(status=status.HTTP_204_NO_CONTENT)


class IntelligenceKnowledgeProcessView(IntelligenceKnowledgeDetailView):
    def post(self, request, project_id, source_id):
        project, source = self.get_object(request, project_id, source_id)
        if not can_edit(request.user, project):
            raise PermissionDenied('Project knowledge edit access is required.')
        if source.status == 'ready':
            return Response(source_payload(source))
        with transaction.atomic():
            source = ProjectKnowledgeSource.objects.select_for_update().get(pk=source.pk)
            process_source(source)
            audit(request.user, 'update', 'ProjectKnowledgeSource', project,
                  {'source_id': source.pk, 'processing_status': source.status})
        return Response(source_payload(source))


class IntelligenceRevisionCompareView(APIView):
    """Compare exact, authorized CRS revision excerpts without AI or writes."""
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id):
        project = project_for(request.user, project_id)
        if not (module_action_allowed(request.user, 'crs_documents', 'read')
                and module_action_allowed(request.user, 'pid_analysis', 'read')):
            raise PermissionDenied('CRS and P&ID read access is required.')
        try:
            source_ids = [int(request.data.get(key)) for key in ('left_source_id', 'right_source_id')]
        except (TypeError, ValueError):
            raise ValidationError('Select two linked CRS revisions.')
        if source_ids[0] == source_ids[1]:
            raise ValidationError('Select two different revisions.')
        sources = list(ProjectKnowledgeSource.objects.filter(
            project=project, pk__in=source_ids, status='ready', crs_revision__isnull=False,
        ).select_related('crs_revision__chain', 'crs_revision__document'))
        if len(sources) != 2 or not all(source_available(source, request.user) for source in sources):
            raise NotFound('Both revisions must be linked and available to this project.')
        if not all(source_hash_matches(source) for source in sources):
            raise KnowledgeSourceChanged()
        by_id = {source.pk: source for source in sources}
        left, right = (by_id[source_ids[0]], by_id[source_ids[1]])
        if left.crs_revision.chain_id != right.crs_revision.chain_id:
            raise ValidationError('Revision comparison requires the same controlled CRS chain.')
        left_lines = [f"[{page['location']}] {line}" for page in left.extracted_pages
                      for line in page['text'].splitlines()]
        right_lines = [f"[{page['location']}] {line}" for page in right.extracted_pages
                       for line in page['text'].splitlines()]
        changes = list(difflib.unified_diff(left_lines[:1500], right_lines[:1500],
                                            fromfile=left.crs_revision.revision_label,
                                            tofile=right.crs_revision.revision_label,
                                            lineterm=''))[:2000]
        return Response({'left': source_payload(left), 'right': source_payload(right),
                         'changes': changes, 'truncated': len(changes) == 2000,
                         'method': 'deterministic_embedded_text_diff'})


def conversation_payload(conversation, *, include_messages=False):
    result = {'id': conversation.pk, 'project_id': conversation.project_id,
              'title': conversation.title, 'archived': bool(conversation.archived_at),
              'created_at': conversation.created_at.isoformat(),
              'updated_at': conversation.updated_at.isoformat()}
    if include_messages:
        result['messages'] = [{
            'id': message.pk, 'role': message.role, 'content': message.content,
            'provider': message.provider, 'model': message.model, 'sources': message.sources,
            'instruction_version': message.instruction_version,
            'created_at': message.created_at.isoformat(),
        } for message in conversation.messages.order_by('created_at', 'pk')]
    return result


def chat_allowed(user):
    if not module_action_allowed(user, 'pid_analysis', 'read'):
        raise PermissionDenied('P&ID Analysis read access is required for chat.')


def conversation_sources_current(conversation, user, *, verify_hash=False):
    source_ids = {item.get('source_id') for message in conversation.messages.exclude(sources=[])
                  for item in message.sources if isinstance(item, dict) and item.get('source_id')}
    if not source_ids:
        return True
    sources = ProjectKnowledgeSource.objects.filter(project_id=conversation.project_id,
                                                    pk__in=source_ids).select_related(
        'project_document', 'crs_revision__document', 'crs_revision__chain')
    by_id = {source.pk: source for source in sources}
    return len(by_id) == len(source_ids) and all(
        source_available(by_id[source_id], user) and
        (not verify_hash or source_hash_matches(by_id[source_id])) for source_id in source_ids)


class IntelligenceConversationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = project_for(request.user, project_id)
        query = IntelligenceConversation.objects.filter(project=project, owner=request.user, archived_at__isnull=True)
        term = request.query_params.get('q', '').strip()
        if term:
            query = query.filter(Q(title__icontains=term) | Q(messages__content__icontains=term)).distinct()
        try:
            page = max(1, min(int(request.query_params.get('page', 1)), 1000))
        except (TypeError, ValueError):
            raise ValidationError({'page': 'Enter a valid page number.'})
        query = query.order_by('-updated_at', '-pk')
        start = (page - 1) * 20
        rows = list(query[start:start + 21])
        return Response({'results': [conversation_payload(row) for row in rows[:20]
                                     if conversation_sources_current(row, request.user)],
                         'next': page + 1 if len(rows) > 20 else None})

    def post(self, request, project_id):
        project = project_for(request.user, project_id)
        chat_allowed(request.user)
        title = str(request.data.get('title') or 'New conversation').strip()[:160]
        with transaction.atomic():
            conversation = IntelligenceConversation.objects.create(
                project=project, owner=request.user, title=title or 'New conversation')
            audit(request.user, 'create', 'IntelligenceConversation', project,
                  {'conversation_id': conversation.pk})
        return Response(conversation_payload(conversation), status=status.HTTP_201_CREATED)


class IntelligenceConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, project_id, conversation_id):
        project = project_for(request.user, project_id)
        conversation = IntelligenceConversation.objects.filter(
            pk=conversation_id, project=project, owner=request.user, archived_at__isnull=True,
        ).first()
        if not conversation:
            raise NotFound('Conversation not found.')
        if not conversation_sources_current(conversation, request.user, verify_hash=True):
            raise NotFound('Conversation source access changed.')
        return project, conversation

    def get(self, request, project_id, conversation_id):
        _, conversation = self.get_object(request, project_id, conversation_id)
        return Response(conversation_payload(conversation, include_messages=True))

    def patch(self, request, project_id, conversation_id):
        project, conversation = self.get_object(request, project_id, conversation_id)
        title = request.data.get('title')
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValidationError({'title': 'Enter a title of 1 to 160 characters.'})
        with transaction.atomic():
            conversation = IntelligenceConversation.objects.select_for_update().get(pk=conversation.pk)
            conversation.title = title.strip()
            conversation.save(update_fields=['title', 'updated_at'])
            audit(request.user, 'update', 'IntelligenceConversation', project,
                  {'conversation_id': conversation.pk})
        return Response(conversation_payload(conversation))

    def delete(self, request, project_id, conversation_id):
        project, conversation = self.get_object(request, project_id, conversation_id)
        with transaction.atomic():
            conversation = IntelligenceConversation.objects.select_for_update().get(pk=conversation.pk)
            conversation.archived_at = timezone.now()
            conversation.save(update_fields=['archived_at', 'updated_at'])
            audit(request.user, 'delete', 'IntelligenceConversation', project,
                  {'conversation_id': conversation.pk})
        return Response(status=status.HTTP_204_NO_CONTENT)


class IntelligenceConversationSendView(IntelligenceConversationDetailView):
    def post(self, request, project_id, conversation_id):
        project, conversation = self.get_object(request, project_id, conversation_id)
        chat_allowed(request.user)
        question = request.data.get('question')
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValidationError({'question': 'Enter a question of 1 to 8000 characters.'})
        try:
            request_id = UUID(str(request.data['request_id'])) if request.data.get('request_id') else uuid4()
        except (TypeError, ValueError):
            raise ValidationError({'request_id': 'A valid UUID is required.'})
        from apps.pid_checker_v2.services.radai_chat import answer_question, ChatConfigurationError
        with transaction.atomic():
            conversation = IntelligenceConversation.objects.select_for_update().get(pk=conversation.pk)
            if conversation.archived_at:
                raise NotFound('Conversation is archived.')
            existing = IntelligenceMessage.objects.filter(conversation=conversation,
                                                          request_id=request_id, role='assistant').first()
            if existing:
                earlier = IntelligenceMessage.objects.get(conversation=conversation,
                                                          request_id=request_id, role='user')
                if earlier.content != question.strip():
                    raise ChatRequestConflict()
                return Response({'conversation': conversation_payload(conversation, include_messages=True),
                                 'token_usage': {}, 'idempotent_replay': True})
            instructions = ProjectAIInstructions.objects.filter(project=project).first()
            preferences = {field: getattr(instructions, field) for field in FIELDS} if instructions else {}
            evidence = retrieve(project, question, request.user)
            context = {'page': 'radai_intelligence', 'project': {
                'id': project.pk, 'code': project.code, 'name': project.name,
            }, 'document_excerpt': '\n\n'.join(
                f"[{item['source_id']} / {item['location']} / {item['approval']}] {item['excerpt']}"
                for item in evidence)[:8000]}
            history = list(conversation.messages.order_by('-created_at', '-pk')[:20])
            history.reverse()
            try:
                result = answer_question(
                    question=question.strip(), context=context,
                    provider=str(request.data.get('provider') or 'claude'),
                    api_key=str(request.data.get('api_key') or ''),
                    history=[{'role': row.role, 'content': row.content} for row in history],
                    project_instructions=preferences,
                )
            except ChatConfigurationError as exc:
                raise ValidationError({'provider': str(exc)})
            except Exception:
                raise ChatServiceUnavailable()
            if not result.get('answer'):
                raise ValidationError('The assistant returned no answer.')
            IntelligenceMessage.objects.create(conversation=conversation, role='user',
                                               content=question.strip(), request_id=request_id)
            assistant = IntelligenceMessage.objects.create(
                conversation=conversation, role='assistant', content=result['answer'],
                provider=result.get('provider', ''), model=result.get('model', ''),
                sources=[{key: value for key, value in item.items() if key != 'excerpt'} for item in evidence],
                instruction_version=instructions.version if instructions else 0,
                request_id=request_id,
            )
            conversation.save(update_fields=['updated_at'])
            audit(request.user, 'create', 'IntelligenceMessage', project,
                  {'conversation_id': conversation.pk, 'message_id': assistant.pk,
                   'source_ids': [item['source_id'] for item in evidence],
                   'provider': result.get('provider'), 'model': result.get('model')})
        return Response({'conversation': conversation_payload(conversation, include_messages=True),
                         'token_usage': result.get('token_usage', {})})


class ChatServiceUnavailable(APIException):
    status_code = 502
    default_detail = 'The assistant is unavailable. Your question was not saved; retry later.'


class ChatRequestConflict(APIException):
    status_code = 409
    default_detail = 'This request ID belongs to a different question.'


class KnowledgeSourceChanged(APIException):
    status_code = 409
    default_detail = 'The source file changed since processing. Reprocess it before using this evidence.'
