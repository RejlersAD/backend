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
from django.test import TestCase, TransactionTestCase, override_settings
from django.db import close_old_connections
from django.core.management import call_command
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.core.ai_workspace_models import (AIWorkspace, AIWorkspaceConversation,
                                           AIWorkspaceDocument, AIWorkspaceMessage, AIWorkspaceAttachment)
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
