"""Secure, signed, idempotent outbound delivery of schedule exports."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import socket
from urllib.parse import urlparse

import httpx
from django.utils import timezone

from .integration_secrets import decrypt_secret
from .schedule_exports import generate_schedule_export


def validate_public_https_url(url, *, resolve_dns=True):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Integration targets must use a credential-free HTTPS URL.')
    if parsed.port and parsed.port != 443:
        raise ValueError('Integration targets must use the standard HTTPS port.')
    addresses = []
    try:
        addresses.append(ipaddress.ip_address(parsed.hostname))
    except ValueError:
        if resolve_dns:
            try:
                addresses.extend({ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(parsed.hostname, 443)})
            except socket.gaierror as exc:
                raise ValueError('Integration target hostname could not be resolved.') from exc
    for address in addresses:
        if not address.is_global:
            raise ValueError('Integration targets cannot resolve to private, loopback, link-local, or reserved addresses.')
    return url


def build_delivery_payload(delivery):
    content, content_type, filename = generate_schedule_export(delivery.version, delivery.endpoint.export_format)
    digest = hashlib.sha256(content).hexdigest()
    if delivery.endpoint.export_format == 'json':
        exported_data = json.loads(content.decode('utf-8'))
        encoding = 'json'
    else:
        exported_data = base64.b64encode(content).decode()
        encoding = 'base64'
    envelope = {
        'schema_version': '1.0', 'event_type': delivery.event_type,
        'request_id': str(delivery.request_id), 'idempotency_key': delivery.idempotency_key,
        'occurred_at': timezone.now().isoformat(),
        'project_id': delivery.version.schedule.project_id,
        'schedule_id': delivery.version.schedule_id, 'version_id': delivery.version_id,
        'export': {
            'format': delivery.endpoint.export_format, 'filename': filename,
            'content_type': content_type, 'encoding': encoding, 'sha256': digest,
            'data': exported_data,
        },
    }
    body = json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return body, digest


def deliver(delivery):
    endpoint = delivery.endpoint
    validate_public_https_url(endpoint.target_url)
    body, digest = build_delivery_payload(delivery)
    headers = {
        'Content-Type': 'application/json', 'User-Agent': 'RADAI-Planning/1.0',
        'Idempotency-Key': delivery.idempotency_key, 'X-RADAI-Request-ID': str(delivery.request_id),
        'X-RADAI-Event': delivery.event_type, 'X-Content-SHA256': digest,
    }
    secret = decrypt_secret(endpoint.secret_encrypted)
    if endpoint.auth_type != 'none' and not secret:
        raise ValueError('The integration credential is unavailable or cannot be decrypted.')
    if endpoint.auth_type == 'bearer':
        headers['Authorization'] = f'Bearer {secret}'
    elif endpoint.auth_type == 'hmac_sha256':
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers['X-RADAI-Signature'] = f'sha256={signature}'
    with httpx.Client(timeout=min(60, max(3, endpoint.timeout_seconds)), follow_redirects=False) as client:
        response = client.post(endpoint.target_url, content=body, headers=headers)
    return response, digest
