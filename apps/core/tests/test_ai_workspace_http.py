"""Corrected AI-project lifecycle through authorized HTTP APIs on disposable PostgreSQL."""
from io import BytesIO
import os
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import time

import pymupdf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import LiveServerTestCase, TestCase, TransactionTestCase, override_settings
from django.db import close_old_connections
from django.core.management import call_command
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.core.ai_workspace_models import (AIWorkspace, AIWorkspaceConversation,
                                           AIWorkspaceDocument, AIWorkspaceMessage, AIWorkspaceAttachment,
                                           AIWorkspaceMember)
from apps.core.project_models import Project
from apps.core.ai_hub_models import AIModel, AIProvider, AIRoute
from apps.core.ai_credential_models import AIProviderConfiguration, AIProviderCredential
from apps.core.ai_credentials import encrypt_api_key
from apps.core.ai_hub_runtime import local_ollama_base_url
from apps.rbac.ai_champion_models import AIUsageLog
from apps.rbac.models import (AuditLog, Organization, Module, Permission, Role,
                              RoleModule, RolePermission, UserProfile, UserRole)
from apps.rbac.module_actions import ensure_module_actions


def enable_local_model():
    provider, _ = AIProvider.objects.get_or_create(pk='ollama', defaults={
        'name': 'Ollama', 'group': 'self', 'protocol': 'ollama', 'auth_kind': 'local',
        'default_base_url': 'http://localhost:11434', 'enabled': True})
    AIModel.objects.update_or_create(provider=provider, model_id='qwen3:8b', defaults={
        'capabilities': ['text'], 'weights': 'open', 'status': 'ga', 'enabled': True})
    AIRoute.objects.update_or_create(task_id='radai-intelligence-chat', defaults={
        'label': 'Local chat', 'module': 'radai_intelligence', 'needs': ['text'],
        'data_policy': 'onprem', 'chain': ['ollama:qwen3:8b']})


def patch_chat_models(rows):
    """Stand-in for chat_models that answers both of its call forms."""
    return patch('apps.core.ai_workspace_views.chat_models',
                 side_effect=lambda user=None, with_restriction=False: (rows, False) if with_restriction else rows)


class AIWorkspaceHTTPTests(TestCase):
    def setUp(self):
        enable_local_model()
        users = get_user_model().objects
        self.owner = users.create_user(username='ai-owner', email='owner@example.test')
        self.colleague = users.create_user(username='ai-colleague', email='colleague@example.test')
        self.outsider = users.create_user(username='ai-outsider', email='outsider@example.test')
        self.storage = TemporaryDirectory(prefix='radai-private-ai-test-')
        self.addCleanup(self.storage.cleanup)
        from apps.core.ai_workspace_models import AIWorkspaceDocument
        file_storage = AIWorkspaceDocument._meta.get_field('file').storage
        old_location = file_storage._location
        file_storage._location = self.storage.name
        self.addCleanup(setattr, file_storage, '_location', old_location)
        attachment_storage = AIWorkspaceAttachment._meta.get_field('file').storage
        old_attachment_location = attachment_storage._location
        attachment_storage._location = self.storage.name
        self.addCleanup(setattr, attachment_storage, '_location', old_attachment_location)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    @override_settings(AI_CREDENTIAL_ENCRYPTION_KEY='synthetic-test-encryption-material')
    def test_managed_claude_chat_uses_selected_key_and_project_evidence(self):
        key = AIProviderCredential.objects.create(
            provider='anthropic', label='Selected Claude',
            encrypted_key=encrypt_api_key('synthetic-anthropic-test-key'))
        config = AIProviderConfiguration.objects.create(
            provider='anthropic', enabled=True, selected_credential=key,
            model='claude-sonnet-4-6')
        owner = self.client_for(self.owner)
        project_id = self.create()
        base = f'/api/v1/ai-workspaces/{project_id}/'
        uploaded = owner.post(f'{base}knowledge/', {'file': self.pdf()}, format='multipart')
        self.assertEqual(uploaded.status_code, 201, uploaded.data)
        models = owner.get('/api/v1/ai-workspaces/models/')
        self.assertIn({'provider': 'anthropic', 'model_id': 'claude-sonnet-4-6',
                       'label': 'Claude (claude-sonnet-4-6)'}, models.data['models'])
        conversation_id = owner.post(f'{base}conversations/', {}, format='json').data['id']
        send_url = f'{base}conversations/{conversation_id}/send/'
        request_id = str(uuid4())
        payload = {'question': 'What material is specified for component P-101?', 'request_id': request_id,
                   'provider': 'anthropic', 'model': 'claude-sonnet-4-6'}
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'ASTM A106 Grade B.', 'provider': 'claude', 'model': 'claude-sonnet-4-6',
            'token_usage': {'input_tokens': 20, 'output_tokens': 6, 'cost_usd': '0.00015'},
        }) as answer:
            sent = owner.post(send_url, payload, format='json')
            self.assertEqual(sent.status_code, 200, sent.data)
            self.assertIn('ASTM A106', answer.call_args.kwargs['context']['document_excerpt'])
            self.assertEqual(answer.call_args.kwargs['provider'], 'claude')
            self.assertEqual(answer.call_args.kwargs['api_key'], 'synthetic-anthropic-test-key')
            self.assertEqual(sent.data['conversation']['messages'][-1]['provider'], 'claude')
            self.assertTrue(sent.data['conversation']['messages'][-1]['sources'])
            self.assertTrue(owner.post(send_url, payload, format='json').data['idempotent_replay'])
            answer.assert_called_once()
        self.assertEqual(AIUsageLog.objects.filter(application='radai-intelligence',
                                                    request_id=request_id).count(), 1)
        attachment = owner.post(f'{base}conversations/{conversation_id}/attachments/',
                                {'file': self.pdf()}, format='multipart')
        self.assertEqual(attachment.status_code, 201, attachment.data)
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'The attachment specifies ASTM A106 Grade B.', 'provider': 'claude',
            'model': 'claude-sonnet-4-6',
            'token_usage': {'input_tokens': 20, 'output_tokens': 8, 'cost_usd': '0.00018'},
        }) as answer:
            attached = owner.post(send_url, {
                **payload, 'request_id': str(uuid4()),
                'attachment_ids': [attachment.data['id']],
            }, format='json')
            self.assertEqual(attached.status_code, 200, attached.data)
            self.assertIn('ASTM A106', answer.call_args.kwargs['context']['document_excerpt'])
            self.assertTrue(any(source['source_id'] == f"attachment-{attachment.data['id']}"
                                for source in attached.data['conversation']['messages'][-1]['sources']))
        general_base = '/api/v1/ai-workspaces/general/conversations/'
        general_id = owner.post(general_base, {}, format='json').data['id']
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'A general answer.', 'provider': 'claude', 'model': 'claude-sonnet-4-6',
            'token_usage': {'input_tokens': 5, 'output_tokens': 5, 'cost_usd': '0.00009'},
        }) as answer:
            general = owner.post(f'{general_base}{general_id}/send/', {
                **payload, 'question': 'Hello', 'request_id': str(uuid4()),
            }, format='json')
            self.assertEqual(general.status_code, 200, general.data)
            self.assertTrue(answer.call_args.kwargs['general_chat'])
            self.assertEqual(general.data['conversation']['messages'][-1]['sources'], [])
        config.enabled = False
        config.save(update_fields=['enabled'])
        self.assertNotIn('anthropic', [row['provider'] for row in owner.get(
            '/api/v1/ai-workspaces/models/').data['models']])
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question') as forbidden:
            denied = owner.post(send_url, {**payload, 'request_id': str(uuid4())}, format='json')
            self.assertEqual(denied.status_code, 400)
            forbidden.assert_not_called()

    def create(self):
        response = self.client_for(self.owner).post('/api/v1/ai-workspaces/', {
            'name': 'Piping Specification Assistant', 'description': 'Private PMS workspace',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['enterprise_project_id'])
        return response.data['id']

    @staticmethod
    def pdf():
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 72), 'PMS piping specification: component P-101 uses ASTM A106 Grade B.')
        result = document.tobytes()
        document.close()
        return SimpleUploadedFile('pms.pdf', result, content_type='application/pdf')

    @staticmethod
    def spreadsheet():
        book = Workbook()
        book.active.title = 'Description template'
        book.active.append(['Component', 'Material description'])
        book.active.append(['P-101', 'ASTM A106 Grade B pipe'])
        output = BytesIO()
        book.save(output)
        return SimpleUploadedFile('template.xlsx', output.getvalue(),
                                  content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_create_upload_grounded_chat_share_revoke_and_audit(self):
        owner = self.client_for(self.owner)
        colleague = self.client_for(self.colleague)
        outsider = self.client_for(self.outsider)
        workspace_id = self.create()
        base = f'/api/v1/ai-workspaces/{workspace_id}/'
        self.assertEqual(owner.get(base).status_code, 200)
        self.assertEqual(owner.get('/api/v1/ai-workspaces/models/').data['default_model'], 'qwen3:8b')
        self.assertEqual(APIClient().get('/api/v1/ai-workspaces/models/').status_code, 401)
        self.assertEqual(outsider.get(base).status_code, 404)
        self.assertEqual(APIClient().get(base).status_code, 401)
        fields = {'role_behavior': 'Use attached company template and cite the specification.',
                  'goals': 'Prepare material descriptions.', 'response_format': 'Brief',
                  'terminology': 'PMS', 'standards': 'PMS',
                  'evidence_requirements': 'Cite page and sheet.', 'constraints': 'Never approve engineering.'}
        instructions = owner.put(f'{base}instructions/', {'expected_version': 0, **fields}, format='json')
        self.assertEqual(instructions.status_code, 200, instructions.data)
        self.assertEqual(instructions.data['version'], 1)
        self.assertEqual(owner.put(f'{base}instructions/', {'expected_version': 0, **fields}, format='json').status_code, 409)
        pdf = owner.post(f'{base}knowledge/', {'file': self.pdf()}, format='multipart')
        sheet = owner.post(f'{base}knowledge/', {'file': self.spreadsheet()}, format='multipart')
        self.assertEqual(pdf.status_code, 201, pdf.data)
        self.assertEqual(sheet.status_code, 201, sheet.data)
        self.assertEqual(pdf.data['status'], 'ready')
        self.assertEqual(sheet.data['status'], 'ready')
        self.assertEqual(pdf.data['approval'], 'unverified')
        self.assertEqual(owner.get(f"{base}knowledge/{pdf.data['id']}/").data['pages'][0]['location'], 'page 1')
        self.assertEqual(owner.get(f"{base}knowledge/{sheet.data['id']}/").data['pages'][0]['location'], 'sheet Description template')
        self.assertEqual(outsider.get(f"{base}knowledge/{pdf.data['id']}/download/").status_code, 404)
        download = owner.get(f"{base}knowledge/{pdf.data['id']}/download/")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(b''.join(download.streaming_content).startswith(b'%PDF-'))
        self.assertEqual(owner.get(f'{base}knowledge/').data['can_edit'], True)
        member = owner.post(f'{base}members/', {'email': self.colleague.email, 'role': 'viewer'}, format='json')
        self.assertEqual(member.status_code, 201, member.data)
        self.assertEqual(owner.patch(base, {'visibility': 'private'}, format='json').status_code, 400)
        self.assertEqual(colleague.get(base).status_code, 200)
        self.assertEqual(colleague.get(f'{base}knowledge/').status_code, 200)
        self.assertEqual(colleague.post(f'{base}knowledge/', {'file': self.pdf()}, format='multipart').status_code, 403)
        self.assertEqual(colleague.put(f'{base}instructions/', {'expected_version': 1, **fields}, format='json').status_code, 403)
        created = owner.post(f'{base}conversations/', {}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        conversation_id = created.data['id']
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question') as forbidden:
            cloud = owner.post(f'{base}conversations/{conversation_id}/send/',
                               {'question': 'Use a paid model', 'provider': 'claude', 'model': 'claude-sonnet'}, format='json')
            self.assertEqual(cloud.status_code, 400)
            forbidden.assert_not_called()
        question = 'Prepare the material description for component P-101 from PMS template.'
        request_id = str(uuid4())
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'Draft: ASTM A106 Grade B pipe, pending engineering review.',
            'provider': 'ollama', 'model': 'qwen3:8b',
            'token_usage': {'input_tokens': 12, 'output_tokens': 8, 'cost_usd': '0.000000'},
        }) as model:
            sent = owner.post(f'{base}conversations/{conversation_id}/send/',
                              {'question': question, 'request_id': request_id}, format='json')
            self.assertEqual(sent.status_code, 200, sent.data)
            self.assertEqual(len(sent.data['conversation']['messages']), 2)
            self.assertEqual(len(sent.data['conversation']['messages'][1]['sources']), 2)
            self.assertIn('PMS piping specification', model.call_args.kwargs['context']['document_excerpt'])
            self.assertIn('Use attached company template', model.call_args.kwargs['project_instructions']['role_behavior'])
            replay = owner.post(f'{base}conversations/{conversation_id}/send/',
                                {'question': question, 'request_id': request_id}, format='json')
            self.assertEqual(replay.status_code, 200)
            self.assertTrue(replay.data['idempotent_replay'])
            model.assert_called_once()
        self.assertEqual(AIWorkspaceMessage.objects.filter(conversation_id=conversation_id).count(), 2)
        self.assertEqual(AIUsageLog.objects.filter(application='radai-intelligence', request_id=request_id).count(), 1)
        self.assertEqual(colleague.get(f'{base}conversations/{conversation_id}/').status_code, 404)
        self.assertEqual(colleague.get(f'{base}conversations/').data['results'], [])
        shared = owner.post(f'{base}conversations/{conversation_id}/share/',
                            {'user_id': self.colleague.pk}, format='json')
        self.assertEqual(shared.status_code, 200)
        self.assertEqual(colleague.get(f'{base}conversations/{conversation_id}/').status_code, 200)
        self.assertEqual(colleague.post(f'{base}conversations/{conversation_id}/send/', {'question': 'Edit it'}, format='json').status_code, 403)
        self.assertEqual(owner.delete(f'{base}members/{self.colleague.pk}/').status_code, 204)
        self.assertEqual(colleague.get(base).status_code, 404)
        self.assertEqual(colleague.get(f'{base}knowledge/').status_code, 404)
        self.assertEqual(colleague.get(f'{base}conversations/{conversation_id}/').status_code, 404)
        self.assertGreaterEqual(AuditLog.objects.filter(resource_type='AIWorkspace', metadata__ai_workspace_id=workspace_id).count(), 6)
        self.assertEqual(AIWorkspace.objects.get(pk=workspace_id).enterprise_project_id, None)

    def test_general_chat_is_owner_only_and_never_retrieves_project_knowledge(self):
        owner = self.client_for(self.owner)
        outsider = self.client_for(self.outsider)
        base = '/api/v1/ai-workspaces/general/conversations/'
        models = owner.get('/api/v1/ai-workspaces/models/')
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.data['models'][0]['label'], 'Qwen3 8B')
        self.assertEqual(APIClient().get(base).status_code, 401)
        project_id = self.create()
        owner.post(f'/api/v1/ai-workspaces/{project_id}/knowledge/', {'file': self.pdf()}, format='multipart')
        created = owner.post(base, {}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        conversation_id = created.data['id']
        detail = f'{base}{conversation_id}/'
        self.assertIsNone(AIWorkspaceConversation.objects.get(pk=conversation_id).workspace_id)
        self.assertEqual(outsider.get(detail).status_code, 404)
        self.assertEqual(outsider.post(f'{detail}send/', {'question': 'P-101?'}, format='json').status_code, 404)
        request_id = str(uuid4())
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'A general answer.', 'provider': 'ollama', 'model': 'qwen3:8b',
            'token_usage': {'input_tokens': 4, 'output_tokens': 5, 'cost_usd': '0'},
        }) as model:
            sent = owner.post(f'{detail}send/', {'question': 'Hello', 'request_id': request_id,
                                                'workspace_id': project_id}, format='json')
            self.assertEqual(sent.status_code, 200, sent.data)
            self.assertEqual(sent.data['conversation']['messages'][1]['sources'], [])
            context = model.call_args.kwargs['context']
            self.assertNotIn('project', context)
            self.assertNotIn('document_excerpt', context)
            self.assertEqual(model.call_args.kwargs['project_instructions'], {})
            self.assertTrue(model.call_args.kwargs['general_chat'])
            replay = owner.post(f'{detail}send/', {'question': 'Hello', 'request_id': request_id}, format='json')
            self.assertTrue(replay.data['idempotent_replay'])
            model.assert_called_once()
        self.assertEqual(owner.get(detail).data['messages'][1]['content'], 'A general answer.')
        self.assertEqual(len(owner.get(base).data['results']), 1)
        self.assertEqual(AIUsageLog.objects.filter(application='radai-intelligence', request_id=request_id).count(), 1)
        self.assertTrue(AuditLog.objects.filter(resource_type='AIWorkspaceConversation',
                                                 metadata__conversation_id=conversation_id).exists())
        self.assertEqual(owner.get(f'/api/v1/ai-workspaces/{project_id}/conversations/').data['results'], [])

    def test_general_pdf_upload_extraction_citations_download_and_isolation(self):
        owner = self.client_for(self.owner)
        outsider = self.client_for(self.outsider)
        base = '/api/v1/ai-workspaces/general/conversations/'
        conversation_id = owner.post(base, {}, format='json').data['id']
        other_id = owner.post(base, {}, format='json').data['id']
        endpoint = f'{base}{conversation_id}/attachments/'
        self.assertEqual(APIClient().post(endpoint, {'file': self.pdf()}, format='multipart').status_code, 401)
        self.assertEqual(outsider.post(endpoint, {'file': self.pdf()}, format='multipart').status_code, 404)
        pdf_file = self.pdf()
        original = pdf_file.read()
        pdf_file.seek(0)
        upload = owner.post(endpoint, {'file': pdf_file}, format='multipart')
        self.assertEqual(upload.status_code, 201, upload.data)
        self.assertEqual(upload.data['extracted_pages'], 1)
        attachment_id = upload.data['id']
        attachment = AIWorkspaceAttachment.objects.get(pk=attachment_id)
        self.assertEqual(attachment.conversation_id, conversation_id)
        self.assertIn('ASTM A106', attachment.extracted_pages[0]['text'])
        download = f'{endpoint}{attachment_id}/download/'
        self.assertEqual(outsider.get(download).status_code, 404)
        self.assertEqual(owner.get(f'{base}{other_id}/attachments/{attachment_id}/download/').status_code, 404)
        self.assertEqual(b''.join(owner.get(download).streaming_content), original)
        request_id = str(uuid4())
        payload = {'question': 'What material is specified for P-101?', 'request_id': request_id,
                   'attachment_ids': [attachment_id]}
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'ASTM A106 Grade B, pms.pdf page 1.', 'provider': 'ollama', 'model': 'qwen3:8b',
            'token_usage': {'input_tokens': 12, 'output_tokens': 8, 'cost_usd': '0'},
        }) as model:
            self.assertEqual(outsider.post(f'{base}{conversation_id}/send/', payload, format='json').status_code, 404)
            self.assertEqual(owner.post(f'{base}{other_id}/send/', payload, format='json').status_code, 400)
            sent = owner.post(f'{base}{conversation_id}/send/', payload, format='json')
            self.assertEqual(sent.status_code, 200, sent.data)
            self.assertIn('ASTM A106', model.call_args.kwargs['context']['document_excerpt'])
            self.assertFalse(model.call_args.kwargs['general_chat'])
        detail = owner.get(f'{base}{conversation_id}/').data
        self.assertEqual(detail['messages'][1]['sources'][0]['location'], 'page 1')
        self.assertEqual(detail['messages'][1]['sources'][0]['sha256'], upload.data['sha256'])
        self.assertEqual(detail['messages'][0]['attachments'][0]['id'], attachment_id)
        self.assertTrue(AuditLog.objects.filter(resource_type='AIWorkspaceConversation',
            metadata__attachment_id=attachment_id).exists())
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'The same evidence specifies ASTM A106 Grade B.',
            'provider': 'ollama', 'model': 'qwen3:8b',
            'token_usage': {'input_tokens': 10, 'output_tokens': 8, 'cost_usd': '0'},
        }) as model:
            follow_up = owner.post(f'{base}{conversation_id}/send/', {
                'question': 'Summarize the attached document.', 'request_id': str(uuid4()),
            }, format='json')
        self.assertEqual(follow_up.status_code, 200, follow_up.data)
        self.assertIn('ASTM A106', model.call_args.kwargs['context']['document_excerpt'])
        self.assertEqual(follow_up.data['conversation']['messages'][-1]['sources'][0]['source_id'],
                         f'attachment-{attachment_id}')

    def test_project_conversation_attachment_stays_private_and_uses_project_context(self):
        owner = self.client_for(self.owner)
        outsider = self.client_for(self.outsider)
        project_id = self.create()
        base = f'/api/v1/ai-workspaces/{project_id}/conversations/'
        conversation_id = owner.post(base, {}, format='json').data['id']
        endpoint = f'{base}{conversation_id}/attachments/'
        self.assertEqual(outsider.post(endpoint, {'file': self.pdf()}, format='multipart').status_code, 404)
        upload = owner.post(endpoint, {'file': self.pdf()}, format='multipart')
        self.assertEqual(upload.status_code, 201, upload.data)
        attachment_id = upload.data['id']
        self.assertEqual(outsider.get(f'{endpoint}{attachment_id}/download/').status_code, 404)
        # Private evidence cannot be exposed by subsequently sharing the conversation.
        owner.patch(f'/api/v1/ai-workspaces/{project_id}/', {'visibility': 'shared'}, format='json')
        owner.post(f'/api/v1/ai-workspaces/{project_id}/members/',
                   {'email': self.colleague.email, 'role': 'viewer'}, format='json')
        self.assertEqual(owner.post(f'{base}{conversation_id}/share/',
                         {'user_id': self.colleague.pk}, format='json').status_code, 403)
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'ASTM A106 Grade B.', 'provider': 'ollama', 'model': 'qwen3:8b',
            'token_usage': {'input_tokens': 10, 'output_tokens': 5, 'cost_usd': '0'},
        }) as model:
            result = owner.post(f'{base}{conversation_id}/send/', {
                'question': 'What material is specified for P-101?', 'attachment_ids': [attachment_id],
                'request_id': str(uuid4()),
            }, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(model.call_args.kwargs['context']['project']['id'], project_id)
        self.assertIn('ASTM A106', model.call_args.kwargs['context']['document_excerpt'])
        self.assertEqual(result.data['conversation']['messages'][1]['sources'][0]['source_id'],
                         f'attachment-{attachment_id}')

    def test_broad_attachment_analysis_selects_representative_pages(self):
        from apps.core.ai_workspace_views import attachment_citation_rows
        owner = self.client_for(self.owner)
        base = '/api/v1/ai-workspaces/general/conversations/'
        conversation_id = owner.post(base, {}, format='json').data['id']
        upload = owner.post(f'{base}{conversation_id}/attachments/', {'file': self.pdf()}, format='multipart')
        attachment = AIWorkspaceAttachment.objects.get(pk=upload.data['id'])
        attachment.extracted_pages = [
            {'location': f'page {index}', 'text': f'Section {index}: requirement {index}.'}
            for index in range(1, 20)]
        attachment.save(update_fields=['extracted_pages'])
        sources = attachment_citation_rows([attachment], 'Analyze this PDF and summarize the entire document.')
        self.assertEqual([row['location'] for row in sources],
                         ['page 1', 'page 10', 'page 19', 'page 5', 'page 15'])
        exact = attachment_citation_rows([attachment], 'What is on page 1?')
        self.assertEqual([row['location'] for row in exact], ['page 1'])
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', return_value={
            'answer': 'Five selected pages were reviewed.', 'provider': 'ollama', 'model': 'qwen3:8b',
            'token_usage': {'input_tokens': 10, 'output_tokens': 8, 'cost_usd': '0'},
        }) as model:
            response = owner.post(f'{base}{conversation_id}/send/', {
                'question': 'Analyze the entire PDF.', 'attachment_ids': [attachment.pk],
                'request_id': str(uuid4()),
            }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['conversation']['messages'][1]['sources']), 5)
        self.assertEqual(response.data['conversation']['messages'][1]['sources'][0]['coverage'],
                         {'selected_pages': 5, 'total_pages': 19, 'partial': True})
        self.assertEqual(model.call_args.kwargs['context']['max_output_tokens'], 160)
        self.assertIn('Source Evidence and Interpretation', model.call_args.kwargs['context']['notes'])

    def test_pdf_without_extractable_text_reports_upload_error(self):
        document = pymupdf.open()
        document.new_page()
        payload = document.tobytes()
        document.close()
        owner = self.client_for(self.owner)
        base = '/api/v1/ai-workspaces/general/conversations/'
        conversation_id = owner.post(base, {}, format='json').data['id']
        response = owner.post(f'{base}{conversation_id}/attachments/', {
            'file': SimpleUploadedFile('scanned.pdf', payload, content_type='application/pdf')}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('No extractable text', str(response.data))
        broken = owner.post(f'{base}{conversation_id}/attachments/', {
            'file': SimpleUploadedFile('broken.pdf', b'%PDF-not a document', content_type='application/pdf')},
            format='multipart')
        self.assertEqual(broken.status_code, 400)
        self.assertIn('could not be parsed', str(broken.data))
        self.assertFalse(AIWorkspaceAttachment.objects.exists())

    def test_docker_host_is_allowed_only_for_local_development(self):
        provider = AIProvider.objects.get(pk='ollama')
        provider.default_base_url = 'http://host.docker.internal:11434'
        provider.save(update_fields=['default_base_url'])
        with override_settings(DEBUG=True, ENVIRONMENT='development'), \
                patch.dict(os.environ, {'AIFLOW_ENVIRONMENT': 'local'}), \
                patch('socket.getaddrinfo', return_value=[(None, None, None, None, ('192.168.65.254', 11434))]):
            self.assertEqual(local_ollama_base_url(), 'http://host.docker.internal:11434')
        with override_settings(DEBUG=False, ENVIRONMENT='production'), \
                patch.dict(os.environ, {'AIFLOW_ENVIRONMENT': 'local'}):
            with self.assertRaises(ValueError):
                local_ollama_base_url()

    def test_cross_project_stale_file_and_failed_upload(self):
        owner = self.client_for(self.owner)
        other = self.client_for(self.outsider)
        first = self.create()
        second = other.post('/api/v1/ai-workspaces/', {'name': 'Other workspace'}, format='json').data['id']
        pdf = owner.post(f'/api/v1/ai-workspaces/{first}/knowledge/', {'file': self.pdf()}, format='multipart')
        self.assertEqual(pdf.status_code, 201)
        doc_id = pdf.data['id']
        self.assertEqual(other.get(f'/api/v1/ai-workspaces/{first}/knowledge/{doc_id}/').status_code, 404)
        self.assertEqual(other.get(f'/api/v1/ai-workspaces/{first}/knowledge/{doc_id}/download/').status_code, 404)
        self.assertEqual(other.get(f'/api/v1/ai-workspaces/{second}/knowledge/{doc_id}/').status_code, 404)
        self.assertEqual(owner.post(f'/api/v1/ai-workspaces/{first}/knowledge/', {
            'file': SimpleUploadedFile('bad.pdf', b'not pdf')}, format='multipart').status_code, 400)
        self.assertEqual(AIWorkspaceDocument.objects.filter(workspace_id=first).count(), 1)
        doc = AIWorkspaceDocument.objects.get(pk=doc_id)
        with doc.file.open('wb') as stream:
            stream.write(b'changed')
        self.assertEqual(owner.get(f'/api/v1/ai-workspaces/{first}/knowledge/{doc_id}/').status_code, 409)
        self.assertEqual(owner.get(f'/api/v1/ai-workspaces/{first}/knowledge/{doc_id}/download/').status_code, 409)
        canonical = Project.objects.create(name='Canonical optional project', code='AI-OPTIONAL-1', owner=self.owner)
        link_url = f'/api/v1/ai-workspaces/{first}/'
        self.assertEqual(owner.patch(link_url, {'enterprise_project_id': canonical.pk}, format='json').status_code, 403)
        organization = Organization.objects.create(name='AI optional link', code='ai-optional-link')
        role = Role.objects.create(name='AI optional link reader', code='ai-optional-link-reader', level=4)
        module, _ = Module.objects.get_or_create(code='project_control', defaults={'name': 'Project Control'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=role, module=module)
        RolePermission.objects.get_or_create(role=role, permission=module.permissions.get(action='read'))
        profile, _ = UserProfile.objects.get_or_create(user=self.owner, defaults={'organization': organization})
        UserRole.objects.filter(user_profile=profile, role__code='default').delete()
        UserRole.objects.get_or_create(user_profile=profile, role=role)
        self.assertEqual(owner.patch(link_url, {'enterprise_project_id': canonical.pk}, format='json').status_code, 200)
        self.assertEqual(AIWorkspace.objects.get(pk=first).enterprise_project_id, canonical.pk)
        self.assertEqual(other.get(link_url).status_code, 404)
        provider = AIProvider.objects.get(pk='ollama')
        provider.default_base_url = 'https://api.example.com'
        provider.save(update_fields=['default_base_url'])
        with self.assertRaises(ValueError):
            local_ollama_base_url()


class AIWorkspaceConcurrencyTests(TransactionTestCase):
    def _fixture_teardown(self):
        call_command('flush', database='default', verbosity=0, interactive=False,
                     allow_cascade=True, inhibit_post_migrate=True)

    def setUp(self):
        call_command('flush', database='default', verbosity=0, interactive=False,
                     allow_cascade=True, inhibit_post_migrate=True)
        enable_local_model()

    def test_same_request_id_commits_one_answer_and_usage_record(self):
        user = get_user_model().objects.create_user(username='ai-concurrent', email='ai-concurrent@example.test')
        workspace = AIWorkspace.objects.create(name='Concurrent AI project', owner=user)
        client = APIClient()
        client.force_authenticate(user=user)
        conversation = client.post(f'/api/v1/ai-workspaces/{workspace.pk}/conversations/', {}, format='json')
        self.assertEqual(conversation.status_code, 201, conversation.data)
        url = f"/api/v1/ai-workspaces/{workspace.pk}/conversations/{conversation.data['id']}/send/"
        body = {'question': 'Explain the project scope.', 'request_id': str(uuid4())}

        def answer(**kwargs):
            time.sleep(.15)
            return {'answer': 'No source evidence was supplied.', 'provider': 'ollama',
                    'model': 'qwen3:8b', 'token_usage': {'input_tokens': 0, 'output_tokens': 0, 'cost_usd': '0'}}

        def send():
            close_old_connections()
            caller = APIClient()
            caller.force_authenticate(user=user)
            response = caller.post(url, body, format='json')
            close_old_connections()
            return response.status_code, response.data

        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=answer) as model:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual([row[0] for row in responses], [200, 200], responses)
        self.assertEqual(sum(bool(row[1].get('idempotent_replay')) for row in responses), 1)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(AIWorkspaceMessage.objects.count(), 2)
        self.assertEqual(AIUsageLog.objects.filter(application='radai-intelligence').count(), 1)


class AIWorkspaceStreamingTests(TransactionTestCase):
    """The send endpoint streams answer text as NDJSON when asked, and saves exactly what it streamed."""

    def _fixture_teardown(self):
        call_command('flush', database='default', verbosity=0, interactive=False,
                     allow_cascade=True, inhibit_post_migrate=True)

    def setUp(self):
        call_command('flush', database='default', verbosity=0, interactive=False,
                     allow_cascade=True, inhibit_post_migrate=True)
        enable_local_model()
        self.user = get_user_model().objects.create_user(username='ai-stream', email='ai-stream@example.test')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        created = self.client.post('/api/v1/ai-workspaces/general/conversations/', {}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.url = f"/api/v1/ai-workspaces/general/conversations/{created.data['id']}/send/"

    @staticmethod
    def lines(response):
        import json
        return [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines() if line]

    def test_streams_deltas_then_saved_conversation(self):
        def answer(**kwargs):
            for piece in ('Hello ', 'there, ', 'engineer.'):
                kwargs['on_delta'](piece)
            return {'answer': 'Hello there, engineer.', 'provider': 'ollama', 'model': 'qwen3:8b',
                    'token_usage': {'input_tokens': 1, 'output_tokens': 3, 'cost_usd': '0'}}

        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=answer):
            response = self.client.post(self.url, {'question': 'Hi', 'request_id': str(uuid4()), 'stream': True},
                                        format='json')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.streaming)
            self.assertEqual(response['X-Accel-Buffering'], 'no')
            events = self.lines(response)
        self.assertEqual([e['text'] for e in events if e['type'] == 'delta'], ['Hello ', 'there, ', 'engineer.'])
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['conversation']['messages'][1]['content'], 'Hello there, engineer.')
        self.assertEqual(AIWorkspaceMessage.objects.filter(role='assistant').count(), 1)
        self.assertEqual(AIUsageLog.objects.filter(application='radai-intelligence').count(), 1)

    def test_errors_before_text_keep_json_status_and_after_text_save_nothing(self):
        missing = self.client.post(self.url, {'question': '', 'stream': True}, format='json')
        self.assertEqual(missing.status_code, 400)
        self.assertIn('question', missing.data)

        def broken(**kwargs):
            kwargs['on_delta']('Partial ')
            raise RuntimeError('provider dropped')

        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=broken):
            response = self.client.post(self.url, {'question': 'Hi', 'stream': True}, format='json')
            events = self.lines(response)
        self.assertEqual(events[0], {'type': 'delta', 'text': 'Partial '})
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['status'], 502)
        self.assertFalse(AIWorkspaceMessage.objects.exists())


    def test_no_fallback_after_text_has_streamed(self):
        models = [{'provider': 'auto', 'model_id': 'auto', 'label': 'Auto'},
                  {'provider': 'ollama', 'model_id': 'qwen3:8b', 'label': 'Qwen'},
                  {'provider': 'openai', 'model_id': 'gpt-4o', 'label': 'OpenAI'}]

        def answer(**kwargs):
            kwargs['on_delta']('Half ')
            raise RuntimeError('quota exceeded')

        with patch_chat_models(models), \
                patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=answer) as model:
            response = self.client.post(self.url, {'question': 'Hi', 'provider': 'auto', 'model': 'auto', 'stream': True},
                                        format='json')
            events = self.lines(response)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertFalse(AIWorkspaceMessage.objects.exists())


class AIWorkspaceLiveStreamingTests(LiveServerTestCase):
    """Over a real HTTP connection, answer text reaches the client while the model is still writing."""

    def _fixture_teardown(self):
        call_command('flush', database='default', verbosity=0, interactive=False,
                     allow_cascade=True, inhibit_post_migrate=True)

    def test_first_words_arrive_before_the_answer_finishes(self):
        import json
        from urllib.request import Request, urlopen
        from rest_framework_simplejwt.tokens import AccessToken
        enable_local_model()
        user = get_user_model().objects.create_user(username='ai-live-stream', email='ai-live-stream@example.test')
        client = APIClient()
        client.force_authenticate(user=user)
        conversation = client.post('/api/v1/ai-workspaces/general/conversations/', {}, format='json').data['id']

        def answer(**kwargs):
            for word in ('One ', 'two ', 'three ', 'four.'):
                kwargs['on_delta'](word)
                time.sleep(.3)
            return {'answer': 'One two three four.', 'provider': 'ollama', 'model': 'qwen3:8b',
                    'token_usage': {'input_tokens': 1, 'output_tokens': 4, 'cost_usd': '0'}}

        request = Request(f'{self.live_server_url}/api/v1/ai-workspaces/general/conversations/{conversation}/send/',
                          data=json.dumps({'question': 'Count', 'stream': True}).encode(), method='POST',
                          headers={'Content-Type': 'application/json',
                                   'Authorization': f'Bearer {AccessToken.for_user(user)}'})
        arrivals = []
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=answer):
            started = time.monotonic()
            with urlopen(request, timeout=30) as response:
                self.assertIn('ndjson', response.headers['Content-Type'])
                for line in response:
                    arrivals.append((time.monotonic() - started, json.loads(line)))
        self.assertEqual([event['type'] for _, event in arrivals], ['delta'] * 4 + ['done'])
        self.assertLess(arrivals[0][0], arrivals[-1][0] - .6)  # the first word came well before the end
        self.assertEqual(arrivals[-1][1]['conversation']['messages'][1]['content'], 'One two three four.')


class ProviderStreamingTests(TestCase):
    """answer_question passes text to on_delta as the provider streams it, and still returns the full result."""

    def test_claude_streams_text_and_returns_final_message(self):
        from types import SimpleNamespace
        from unittest import mock
        from apps.pid_checker_v2.services import radai_chat
        usage = SimpleNamespace(input_tokens=7, output_tokens=3, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        final = SimpleNamespace(stop_reason='end_turn', usage=usage,
                                content=[SimpleNamespace(type='text', text='Relief valves protect vessels.')])
        stream = mock.MagicMock()
        stream.__enter__.return_value = SimpleNamespace(text_stream=iter(['Relief ', 'valves ', 'protect vessels.']),
                                                        get_final_message=lambda: final)
        client = mock.Mock()
        client.messages.stream.return_value = stream
        pieces = []
        with mock.patch('anthropic.Anthropic', return_value=client):
            result = radai_chat.answer_question(question='Why relief valves?', context={}, provider='claude',
                                                api_key='test-key', model='claude-sonnet-4-6', on_delta=pieces.append)
        self.assertEqual(pieces, ['Relief ', 'valves ', 'protect vessels.'])
        self.assertEqual(result['answer'], 'Relief valves protect vessels.')
        client.messages.create.assert_not_called()

    def test_ollama_streams_lines_and_keeps_token_counts(self):
        import json
        from unittest import mock
        from apps.pid_checker_v2.services import radai_chat
        lines = [json.dumps({'message': {'content': 'Local '}}).encode(),
                 json.dumps({'message': {'content': 'answer.'}}).encode(),
                 json.dumps({'message': {'content': ''}, 'done': True, 'prompt_eval_count': 9, 'eval_count': 2}).encode()]
        response = mock.Mock()
        response.iter_lines.return_value = iter(lines)
        session = mock.MagicMock()
        session.__enter__.return_value.post.return_value = response
        pieces = []
        with mock.patch('requests.Session', return_value=session),                 mock.patch('apps.core.ai_hub_runtime.local_ollama_base_url', return_value='http://ollama.test'):
            result = radai_chat.answer_question(question='Hi', context={}, provider='ollama', api_key='',
                                                model='qwen3:8b', on_delta=pieces.append)
        sent = session.__enter__.return_value.post.call_args
        self.assertTrue(sent.kwargs['json']['stream'])
        self.assertTrue(sent.kwargs['stream'])
        self.assertEqual(pieces, ['Local ', 'answer.'])
        self.assertEqual(result['answer'], 'Local answer.')
        self.assertEqual((result['token_usage']['input_tokens'], result['token_usage']['output_tokens']), (9, 2))


class ModelChoiceAndFallbackTests(TestCase):
    """OpenAI and Gemini join the menu with Auto, and a provider that runs out hands over to the next."""

    MODELS = [{'provider': 'auto', 'model_id': 'auto', 'label': 'Auto (RADAI picks)'},
              {'provider': 'anthropic', 'model_id': 'claude-sonnet-4-6', 'label': 'Claude'},
              {'provider': 'openai', 'model_id': 'gpt-4o', 'label': 'OpenAI'},
              {'provider': 'gemini', 'model_id': 'gemini-2.5-flash', 'label': 'Gemini'}]

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='ai-fallback', email='ai-fallback@example.test')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        created = self.client.post('/api/v1/ai-workspaces/general/conversations/', {}, format='json')
        self.url = f"/api/v1/ai-workspaces/general/conversations/{created.data['id']}/send/"
        models = patch_chat_models(self.MODELS)
        models.start()
        self.addCleanup(models.stop)
        for target, value in (('apps.core.ai_workspace_views.resolve_provider_credential', ('claude-key', {})),
                              ('apps.core.ai_workspace_views.get_provider_configuration', {'ready': True})):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_model_list_adds_configured_openai_and_gemini_and_auto(self):
        from apps.core import ai_workspace_views
        configs = {'anthropic': {'provider': 'anthropic', 'managed': True, 'ready': True, 'model': 'claude-sonnet-4-6'},
                   'openai': {'provider': 'openai', 'managed': True, 'ready': True, 'model': ''},
                   'gemini': {'provider': 'gemini', 'managed': True, 'ready': False, 'model': 'gemini-2.5-pro'}}
        patch.stopall()
        with patch('apps.core.ai_workspace_views.get_provider_configuration', side_effect=configs.get):
            rows = ai_workspace_views.chat_models(self.user)
        self.assertEqual([(row['provider'], row['model_id']) for row in rows],
                         [('auto', 'auto'), ('anthropic', 'claude-sonnet-4-6'), ('openai', 'gpt-4o')])

    def test_candidates_put_the_choice_first_and_keep_web_search_on_claude(self):
        from apps.core.ai_workspace_views import chat_candidates
        order = lambda rows: [row['provider'] for row in rows]
        self.assertEqual(order(chat_candidates(self.MODELS, 'auto', 'auto')), ['anthropic', 'openai', 'gemini'])
        self.assertEqual(order(chat_candidates(self.MODELS, 'gemini', 'gemini-2.5-flash')), ['gemini', 'anthropic', 'openai'])
        self.assertEqual(order(chat_candidates(self.MODELS, 'auto', 'auto', web_search=True)), ['anthropic'])

    def test_failure_reasons(self):
        from types import SimpleNamespace
        from apps.core.ai_workspace_views import failure_reason
        from apps.pid_checker_v2.services.radai_chat import ChatConfigurationError
        self.assertEqual(failure_reason(SimpleNamespace(status_code=429)), 'limit')
        self.assertEqual(failure_reason(RuntimeError('Your credit balance is too low')), 'limit')
        self.assertEqual(failure_reason(RuntimeError('429 RESOURCE_EXHAUSTED')), 'limit')
        self.assertEqual(failure_reason(SimpleNamespace(status_code=401)), 'credentials')
        self.assertEqual(failure_reason(ChatConfigurationError('no key')), 'not_configured')
        self.assertEqual(failure_reason(RuntimeError('overloaded')), 'unavailable')

    def test_general_chat_offers_only_gemini_flash(self):
        listed = self.client.get('/api/v1/ai-workspaces/models/')
        self.assertEqual([(row['provider'], row['model_id']) for row in listed.data['models']],
                         [('gemini', 'gemini-2.5-flash')])
        self.assertEqual((listed.data['scope'], listed.data['default_model']), ('general', 'gemini-2.5-flash'))
        refused = self.client.post(self.url, {'question': 'Hi', 'provider': 'anthropic', 'model': 'claude-sonnet-4-6'},
                                   format='json')
        self.assertEqual(refused.status_code, 400)

    def test_general_chat_uses_flash_even_when_a_pro_model_is_configured(self):
        from apps.core.ai_workspace_views import conversation_models
        rows = [{'provider': 'gemini', 'model_id': 'gemini-2.5-pro', 'label': 'Gemini'}]
        with patch_chat_models(rows):
            menu, pool, _restricted = conversation_models(self.user)
        self.assertEqual([row['model_id'] for row in menu], ['gemini-2.5-flash'])

    def test_claude_limit_falls_back_to_openai_and_is_recorded(self):
        calls = []

        def answer(**kwargs):
            calls.append(kwargs['provider'])
            if kwargs['provider'] == 'gemini':
                raise RuntimeError('429 RESOURCE_EXHAUSTED')
            if kwargs['provider'] == 'claude':
                raise RuntimeError('Your credit balance is too low to access the Anthropic API.')
            return {'answer': 'Answered by OpenAI.', 'provider': 'openai', 'model': 'gpt-4o',
                    'token_usage': {'input_tokens': 2, 'output_tokens': 3, 'cost_usd': '0'}}

        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=answer):
            sent = self.client.post(self.url, {'question': 'Hi', 'provider': 'gemini', 'model': 'gemini-2.5-flash'},
                                    format='json')
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(calls, ['gemini', 'claude', 'openai'])  # general chat: Gemini Flash first
        saved = sent.data['conversation']['messages'][1]
        self.assertEqual((saved['provider'], saved['model'], saved['content']), ('openai', 'gpt-4o', 'Answered by OpenAI.'))
        self.assertEqual(saved['token_usage']['fallback_from'],
                         [{'provider': 'gemini', 'model': 'gemini-2.5-flash', 'reason': 'limit'},
                          {'provider': 'anthropic', 'model': 'claude-sonnet-4-6', 'reason': 'limit'}])
        self.assertEqual(AIUsageLog.objects.get(request_id__isnull=False).provider, 'openai')

    def test_explicit_choice_also_falls_back_but_fails_when_every_model_fails(self):
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=RuntimeError('down')) as model:
            sent = self.client.post(self.url, {'question': 'Hi', 'provider': 'gemini', 'model': 'gemini-2.5-flash'},
                                    format='json')
        self.assertEqual(sent.status_code, 502)
        self.assertEqual([call.kwargs['provider'] for call in model.call_args_list], ['gemini', 'claude', 'openai'])
        self.assertFalse(AIWorkspaceMessage.objects.exists())


class GeminiAndOpenAIStreamingTests(TestCase):
    def test_gemini_streams_text_with_usage(self):
        from types import SimpleNamespace
        from unittest import mock
        from apps.pid_checker_v2.services import radai_chat
        usage = SimpleNamespace(prompt_token_count=11, candidates_token_count=4)
        chunks = [SimpleNamespace(text='Gemini ', usage_metadata=None),
                  SimpleNamespace(text='answer.', usage_metadata=usage)]
        client = mock.Mock()
        client.models.generate_content_stream.return_value = iter(chunks)
        pieces = []
        with mock.patch('google.genai.Client', return_value=client):
            result = radai_chat.answer_question(question='Hi', context={}, provider='gemini', api_key='g-key',
                                                model='gemini-2.5-flash', on_delta=pieces.append)
        self.assertEqual(pieces, ['Gemini ', 'answer.'])
        self.assertEqual(result['answer'], 'Gemini answer.')
        self.assertEqual((result['token_usage']['input_tokens'], result['token_usage']['output_tokens']), (11, 4))
        sent = client.models.generate_content_stream.call_args.kwargs
        self.assertEqual(sent['contents'][-1].role, 'user')

    def test_openai_streams_text_with_usage(self):
        from types import SimpleNamespace
        from unittest import mock
        from apps.pid_checker_v2.services import radai_chat
        delta = lambda text: SimpleNamespace(delta=SimpleNamespace(content=text))
        chunks = [SimpleNamespace(choices=[delta('Open ')], usage=None),
                  SimpleNamespace(choices=[delta('answer.')], usage=None),
                  SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=6, completion_tokens=2))]
        client = mock.Mock()
        client.chat.completions.create.return_value = iter(chunks)
        pieces = []
        with mock.patch('openai.OpenAI', return_value=client):
            result = radai_chat.answer_question(question='Hi', context={}, provider='openai', api_key='o-key',
                                                model='gpt-4o', on_delta=pieces.append)
        self.assertEqual(pieces, ['Open ', 'answer.'])
        self.assertEqual(result['answer'], 'Open answer.')
        self.assertEqual((result['token_usage']['input_tokens'], result['token_usage']['output_tokens']), (6, 2))
        self.assertTrue(client.chat.completions.create.call_args.kwargs['stream'])


class ProjectModelsAndUsageTests(TestCase):
    """Project owners choose the models; usage totals add up tokens and cost in USD and AED."""

    MODELS = ModelChoiceAndFallbackTests.MODELS

    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username='ai-models-owner', email='ai-models-owner@example.test')
        self.member = users.create_user(username='ai-models-member', email='ai-models-member@example.test')
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.project = AIWorkspace.objects.create(name='Unit 100', owner=self.owner, visibility='shared')
        AIWorkspaceMember.objects.create(workspace=self.project, user=self.member, role='editor', added_by=self.owner)
        self.base = f'/api/v1/ai-workspaces/{self.project.pk}/'
        models = patch_chat_models(self.MODELS)
        models.start()
        self.addCleanup(models.stop)
        for target, value in (('apps.core.ai_workspace_views.resolve_provider_credential', ('claude-key', {})),
                              ('apps.core.ai_workspace_views.get_provider_configuration', {'ready': True}),
                              ('apps.core.ai_workspace_views.module_allowed', True)):  # RBAC grants RADAI Intelligence
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def choose(self, rows, default=''):
        return self.client.patch(self.base, {'chat_models': rows, 'default_chat_model': default}, format='json')

    def test_owner_chooses_models_and_members_only_get_that_list(self):
        saved = self.choose([{'provider': 'openai', 'model_id': 'gpt-5'},
                             {'provider': 'anthropic', 'model_id': 'claude-opus-4-5'}], default='claude-opus-4-5')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['default_chat_model'], 'claude-opus-4-5')
        member = APIClient()
        member.force_authenticate(user=self.member)
        listed = member.get(f'/api/v1/ai-workspaces/models/?project={self.project.pk}')
        self.assertEqual([row['model_id'] for row in listed.data['models']], ['claude-opus-4-5', 'gpt-5', 'auto'])
        self.assertEqual((listed.data['default_model'], listed.data['scope']), ('claude-opus-4-5', 'project'))
        self.assertEqual(member.patch(self.base, {'chat_models': []}, format='json').status_code, 403)

        conversation = member.post(f'{self.base}conversations/', {}, format='json').data['id']
        url = f'{self.base}conversations/{conversation}/send/'
        self.assertEqual(member.post(url, {'question': 'Hi', 'provider': 'gemini', 'model': 'gemini-2.5-flash'},
                                     format='json').status_code, 400)
        with patch('apps.pid_checker_v2.services.radai_chat.answer_question', side_effect=RuntimeError('down')) as model:
            member.post(url, {'question': 'Hi', 'provider': 'openai', 'model': 'gpt-5'}, format='json')
        # Fallback stays inside the project's list.
        self.assertEqual([(call.kwargs['provider'], call.kwargs['model']) for call in model.call_args_list],
                         [('openai', 'gpt-5'), ('claude', 'claude-opus-4-5')])

    def test_choice_is_validated_and_can_be_cleared(self):
        self.assertEqual(self.choose([{'provider': 'mistral', 'model_id': 'large'}]).status_code, 400)
        self.assertEqual(self.choose([{'provider': 'openai', 'model_id': 'gpt 5; drop'}]).status_code, 400)
        saved = self.choose([{'provider': 'gemini', 'model_id': 'gemini-2.5-pro'}], default='not-in-list')
        self.assertEqual(saved.data['default_chat_model'], 'gemini-2.5-pro')
        cleared = self.choose([])
        self.assertEqual((cleared.data['chat_models'], cleared.data['default_chat_model']), ([], ''))
        listed = self.client.get(f'/api/v1/ai-workspaces/models/?project={self.project.pk}')
        self.assertEqual(listed.data['models'][0]['provider'], 'auto')  # back to the organization's models

    def test_usage_totals_in_usd_and_aed(self):
        conversation = AIWorkspaceConversation.objects.create(workspace=self.project, owner=self.owner, title='Usage')
        for tokens, cost in ((1000, '0.010000'), (500, '0.002500')):
            AIWorkspaceMessage.objects.create(conversation=conversation, role='assistant', content='ok',
                                              token_usage={'input_tokens': tokens, 'output_tokens': tokens // 2,
                                                           'cost_usd': cost})
        usage = self.client.get(f'/api/v1/ai-workspaces/usage/?project={self.project.pk}&conversation={conversation.pk}')
        self.assertEqual(usage.status_code, 200, usage.data)
        chat = usage.data['conversation']
        self.assertEqual((chat['input_tokens'], chat['output_tokens'], chat['total_tokens'], chat['answers']),
                         (1500, 750, 2250, 2))
        self.assertEqual(chat['cost'], {'usd': '0.0125', 'aed': '0.0459'})
        self.assertEqual(usage.data['project']['total_tokens'], 2250)
        self.assertEqual(usage.data['month']['total_tokens'], 2250)
        member = APIClient()
        member.force_authenticate(user=self.member)
        self.assertNotIn('project', member.get(f'/api/v1/ai-workspaces/usage/?project={self.project.pk}').data)
        self.assertEqual(member.get(f'/api/v1/ai-workspaces/usage/?conversation={conversation.pk}').status_code, 404)

    def test_prices_cover_gemini_and_model_families(self):
        from apps.pid_checker_v2.services.token_accounting import price_lookup
        self.assertEqual(price_lookup('gemini', 'gemini-2.5-flash')['input'], 0.30)
        self.assertEqual(price_lookup('openai', 'gpt-5-mini-2025-08-07')['output'], 2.00)
        self.assertEqual(price_lookup('gemini', 'gemini-9-unknown')['input'], 0.50)


class ProjectInvitationTests(TestCase):
    """Adding someone invites them; they join from Approvals, and RBAC must include RADAI Intelligence."""

    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username='invite-owner', email='invite-owner@example.test',
                                       first_name='Olivia', last_name='Owner')
        self.guest = users.create_user(username='invite-guest', email='invite-guest@example.test')
        self.other = users.create_user(username='invite-other', email='invite-other@example.test')
        self.project = AIWorkspace.objects.create(name='Train 4 tie-ins', description='CRS close-out for Train 4.',
                                                  owner=self.owner, folder='Process')
        self.owner_client, self.guest_client = APIClient(), APIClient()
        self.owner_client.force_authenticate(user=self.owner)
        self.guest_client.force_authenticate(user=self.guest)
        self.rbac = {self.owner.pk: True, self.guest.pk: True, self.other.pk: True}
        allowed = patch('apps.core.ai_workspace_views.module_allowed',
                        side_effect=lambda user: self.rbac.get(user.pk, False))
        allowed.start()
        self.addCleanup(allowed.stop)
        notified = patch('apps.notifications.services.NotificationService.create_notification')
        self.notify = notified.start()
        self.addCleanup(notified.stop)

    def invite(self, role='viewer'):
        with self.captureOnCommitCallbacks(execute=True):
            return self.owner_client.post(f'/api/v1/ai-workspaces/{self.project.pk}/members/',
                                          {'user_id': self.guest.pk, 'role': role}, format='json')

    def pending(self):
        return self.guest_client.get('/api/v1/ai-workspaces/invitations/').data['results']

    def decide(self, invitation, decision, client=None, **body):
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.guest_client).post(
                f"/api/v1/ai-workspaces/invitations/{invitation['id']}/{decision}/", body, format='json')

    def test_invitation_notifies_with_project_details_and_grants_nothing_yet(self):
        added = self.invite('editor')
        self.assertEqual((added.status_code, added.data['status'], added.data['warning']), (201, 'invited', ''))
        kwargs = self.notify.call_args.kwargs
        self.assertEqual((kwargs['recipient'], kwargs['category'], kwargs['action_url'], kwargs['send_email']),
                         (self.guest, 'APPROVAL', '/approvals', True))
        self.assertIn('Train 4 tie-ins', kwargs['title'])
        self.assertIn('Olivia Owner invited you', kwargs['message'])
        # No access until accepted.
        self.assertEqual(self.guest_client.get(f'/api/v1/ai-workspaces/{self.project.pk}/').status_code, 404)
        self.assertEqual(self.guest_client.get('/api/v1/ai-workspaces/').data['results'], [])
        [invitation] = self.pending()
        self.assertEqual((invitation['role'], invitation['requester_name'], invitation['project']['name'],
                          invitation['project']['description'], invitation['project']['owner_email']),
                         ('editor', 'Olivia Owner', 'Train 4 tie-ins', 'CRS close-out for Train 4.', self.owner.email))
        self.assertIn('Upload and remove knowledge files', invitation['access'])
        members = self.owner_client.get(f'/api/v1/ai-workspaces/{self.project.pk}/members/').data['results']
        self.assertEqual([row['status'] for row in members], ['active', 'invited'])

    def test_owner_is_warned_when_the_person_lacks_rbac_access(self):
        self.rbac[self.guest.pk] = False
        added = self.invite()
        self.assertEqual(added.data['status'], 'invited')
        self.assertIn('Roles & Access', added.data['warning'])

    def test_accept_grants_the_role_and_tells_the_owner(self):
        self.invite('viewer')
        [invitation] = self.pending()
        accepted = self.decide(invitation, 'accept')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data['project']['role'], 'viewer')
        self.assertEqual(self.guest_client.get(f'/api/v1/ai-workspaces/{self.project.pk}/').data['role'], 'viewer')
        self.assertEqual(self.notify.call_args.kwargs['recipient'], self.owner)
        self.assertEqual(self.pending(), [])
        # Viewer level: can't change instructions.
        self.assertEqual(self.guest_client.put(f'/api/v1/ai-workspaces/{self.project.pk}/instructions/',
                                               {'goals': 'x', 'expected_version': 0}, format='json').status_code, 403)

    def test_accepting_needs_the_rbac_module_and_revoking_it_removes_access(self):
        self.invite()
        [invitation] = self.pending()
        self.rbac[self.guest.pk] = False
        self.assertFalse(self.pending()[0]['rbac_access'])
        refused = self.decide(invitation, 'accept')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('RADAI Intelligence access', str(refused.data['detail']))
        self.rbac[self.guest.pk] = True
        self.assertEqual(self.decide(invitation, 'accept').status_code, 200)
        self.assertEqual(self.guest_client.get(f'/api/v1/ai-workspaces/{self.project.pk}/').status_code, 200)
        self.rbac[self.guest.pk] = False  # an administrator revokes the module
        self.assertEqual(self.guest_client.get(f'/api/v1/ai-workspaces/{self.project.pk}/').status_code, 404)

    def test_decline_cancel_and_other_people(self):
        self.invite()
        [invitation] = self.pending()
        other = APIClient()
        other.force_authenticate(user=self.other)
        self.assertEqual(self.decide(invitation, 'accept', client=other).status_code, 404)
        declined = self.decide(invitation, 'decline', reason='Not on this project')
        self.assertEqual(declined.status_code, 200)
        self.assertIn('Not on this project', self.notify.call_args.kwargs['message'])
        self.assertEqual(self.notify.call_args.kwargs['title'], 'Project invitation declined: Train 4 tie-ins')
        self.assertFalse(AIWorkspaceMember.objects.filter(user=self.guest).exists())
        self.invite()
        self.assertEqual(self.owner_client.delete(
            f'/api/v1/ai-workspaces/{self.project.pk}/members/{self.guest.pk}/').status_code, 204)
        self.assertEqual(self.pending(), [])

    def test_existing_members_keep_access_and_only_change_role(self):
        AIWorkspaceMember.objects.create(workspace=self.project, user=self.guest, role='viewer', added_by=self.owner)
        self.project.visibility = 'shared'
        self.project.save()
        changed = self.invite('editor')
        self.assertEqual((changed.status_code, changed.data['status']), (200, 'active'))
        self.notify.assert_not_called()
        self.assertEqual(self.guest_client.get(f'/api/v1/ai-workspaces/{self.project.pk}/').data['role'], 'editor')

    def test_unknown_decision_is_refused(self):
        self.invite()
        [invitation] = self.pending()
        self.assertEqual(self.decide(invitation, 'approve-all').status_code, 404)
