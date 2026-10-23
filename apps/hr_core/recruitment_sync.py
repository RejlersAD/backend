"""Read the same public WordPress feed used by Rejlers AE's careers widget."""
import math
import uuid
from datetime import timedelta
from html import unescape
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.response import Response

from .governance import audit
from .models import RecruitmentOpening, RecruitmentPositionSync
from .recruitment_forms import HRFormsView

SOURCE = 'https://www.rejlers.com/ae/careers/'
FEED = 'https://www.rejlers.com/ae/wp-json/wp/v2/rls_openposition'


def fetch_positions():
    rows, identifiers, expected_total, expected_pages = [], set(), None, None
    for page in range(1, 101):
        response = requests.get(FEED, params={'per_page': 100, 'page': page, '_fields': 'id,title,link,knowit_custom'},
                                timeout=(5, 25), allow_redirects=False, headers={'Accept': 'application/json', 'User-Agent': 'RADAI-Careers-Sync/1.0'})
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise ValueError('The careers feed returned an unavailable or oversized response.')
        try:
            total = int(response.headers['X-WP-Total'])
            pages = int(response.headers['X-WP-TotalPages'])
            data = response.json()
        except (KeyError, TypeError, ValueError):
            raise ValueError('The careers feed is missing valid pagination metadata.')
        if not isinstance(data, list) or not 0 <= total <= 10000 or pages != math.ceil(total / 100):
            raise ValueError('The careers feed returned unexpected pagination data.')
        if page == 1:
            expected_total, expected_pages = total, pages
        if (total, pages) != (expected_total, expected_pages):
            raise ValueError('The careers feed changed during synchronization; retry on the next run.')
        for item in data:
            if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0 or item['id'] in identifiers:
                raise ValueError('The careers feed returned invalid or duplicate position identifiers.')
            title_value = item.get('title', {})
            custom = item.get('knowit_custom', {})
            title = title_value.get('rendered') if isinstance(title_value, dict) else None
            location = custom.get('location') if isinstance(custom, dict) else None
            url = item.get('link')
            if not all(isinstance(value, str) and value.strip() for value in (title, location, url)):
                raise ValueError('A source position is missing its title, location or link.')
            title, location = unescape(strip_tags(title)).strip(), unescape(strip_tags(location)).strip()
            parsed = urlsplit(url)
            if not title or not location or max(len(title), len(location)) > 200 or len(url) > 1000 or parsed.scheme != 'https' or parsed.netloc != 'www.rejlers.com' or not parsed.path.startswith('/ae/careers/open-positions/'):
                raise ValueError('The careers feed returned an unsupported position record.')
            identifiers.add(item['id'])
            rows.append({'source_id': item['id'], 'title': title, 'location': location, 'source_url': url})
        if page >= max(1, expected_pages):
            if len(rows) != expected_total:
                raise ValueError('The careers feed returned an incomplete vacancy list.')
            return rows
    raise ValueError('The careers feed exceeded the supported page limit.')


def sync_positions(actor=None):
    now, lease = timezone.now(), uuid.uuid4()
    with transaction.atomic():
        RecruitmentPositionSync.objects.get_or_create(source='rejlers_ae')
        state = RecruitmentPositionSync.objects.select_for_update().get(source='rejlers_ae')
        if state.lease_expires_at and state.lease_expires_at > now:
            return {'status': 'running', 'detail': 'A careers synchronization is already running.'}
        state.lease_token, state.lease_expires_at = lease, now + timedelta(minutes=5)
        state.last_attempt_at, state.status, state.error = now, 'running', ''
        state.save()
    try:
        records = fetch_positions()
        with transaction.atomic():
            state = RecruitmentPositionSync.objects.select_for_update().get(source='rejlers_ae')
            if state.lease_token != lease:
                return {'status': 'superseded'}
            counts = {'fetched': len(records), 'created': 0, 'updated': 0, 'closed': 0}
            seen_at = timezone.now()
            for record in records:
                _, created = RecruitmentOpening.objects.update_or_create(source_id=record['source_id'], defaults={
                    **record, 'source': 'rejlers_careers', 'is_open': True, 'last_seen_at': seen_at})
                counts['created' if created else 'updated'] += 1
            counts['closed'] = RecruitmentOpening.objects.filter(source='rejlers_careers', is_open=True).exclude(source_id__in=[row['source_id'] for row in records]).update(is_open=False, updated_at=seen_at)
            state.status, state.error, state.counts, state.last_success_at = 'success', '', counts, seen_at
            state.lease_token = state.lease_expires_at = None
            state.save()
            audit(actor=actor, action='recruitment.positions.sync', object_type='recruitment_openings', metadata=counts)
        return {'status': 'success', **counts}
    except Exception:
        # Do not store remote HTML, exception payloads or partial vacancy data.
        error = 'Careers synchronization failed. The previous positions are unchanged. Check source availability and retry.'
        RecruitmentPositionSync.objects.filter(source='rejlers_ae', lease_token=lease).update(status='failed', error=error, lease_token=None, lease_expires_at=None)
        return {'status': 'failed', 'detail': error}


class RecruitmentPositionSyncView(HRFormsView):
    @property
    def permission_action(self):
        return 'read' if self.request.method in ('GET', 'HEAD') else 'update'

    def get(self, request):
        state = RecruitmentPositionSync.objects.filter(source='rejlers_ae').first()
        return Response({'source_url': SOURCE, 'automatic_enabled': settings.RECRUITMENT_CAREERS_SYNC_ENABLED,
            'interval_seconds': settings.RECRUITMENT_CAREERS_SYNC_INTERVAL_SECONDS,
            'status': state.status if state else 'not_synced', 'last_attempt_at': state.last_attempt_at if state else None,
            'last_success_at': state.last_success_at if state else None, 'error': state.error if state else '', 'counts': state.counts if state else {}})

    def post(self, request):
        result = sync_positions(actor=request.user)
        return Response(result, status=502 if result['status'] == 'failed' else 202 if result['status'] == 'running' else 200)
