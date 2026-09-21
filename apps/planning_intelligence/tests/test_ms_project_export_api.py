"""Native exchange uses the existing authenticated, project-scoped export route."""
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from rest_framework.test import APIClient
from django.utils import timezone

from ..models import ScheduleExportRecord, ScheduleBaseline
from ..services.cpm import calculate_schedule_version
from ..services.planning_boundaries import freeze_schedule_inputs
from ..schedule_serializers import ScheduleActivitySerializer, ScheduleVersionSerializer
from .test_scheduling_engine import ScheduleAPIFixture


class MicrosoftProjectExportAPITests(ScheduleAPIFixture):
    def setUp(self):
        super().setUp()
        self.calendar.working_times = {str(day): [{'from': '08:00:00', 'to': '16:00:00'}] for day in range(5)}
        self.calendar.save(update_fields=['working_times', 'updated_at'])
        self.activity('EXP-NATIVE', 2)
        calculate_schedule_version(self.version)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/'

    def test_xml_alias_and_bundle_are_audited_with_real_format_and_digest(self):
        response = self.client.get(self.url + 'export/', {'export_format': 'ms_project_xml'})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        record = ScheduleExportRecord.objects.get(version=self.version)
        self.assertEqual(record.export_format, 'mspdi')
        self.assertTrue(record.filename.endswith('.xml'))
        self.assertEqual(record.sha256, sha256(response.content).hexdigest())
        self.assertEqual(response['X-Content-SHA256'], record.sha256)
        self.assertTrue(response['Content-Type'].startswith('application/xml'))
        response = self.client.get(self.url + 'export/', {'export_format': 'mspdi_zip'})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        record = ScheduleExportRecord.objects.get(version=self.version, export_format='mspdi_zip')
        self.assertTrue(record.filename.endswith('.zip'))
        with ZipFile(BytesIO(response.content)) as archive:
            report = json.loads(archive.read('verification.json'))
            self.assertEqual(report['vendor_application_roundtrip'], 'not_tested')

    def test_outsider_cannot_download_or_read_capabilities_or_create_export_record(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.url + 'export/', {'export_format': 'mspdi_zip'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(self.url + 'export-capabilities/').status_code, 404)
        self.assertFalse(ScheduleExportRecord.objects.filter(version=self.version).exists())
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url + 'export/', {'export_format': 'mspdi'}).status_code, {401, 403})

    def test_missing_working_intervals_returns_actionable_issue_without_export_record(self):
        self.calendar.working_times = {}
        self.calendar.save(update_fields=['working_times', 'updated_at'])
        response = self.client.get(self.url + 'export/', {'export_format': 'mspdi_zip'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('mspdi_working_times_required', {row['code'] for row in response.data['issues']})
        self.assertFalse(ScheduleExportRecord.objects.filter(version=self.version).exists())

    def test_baseline_native_export_uses_frozen_shifts_names_and_risks(self):
        self.version.refresh_from_db()
        frozen_inputs = freeze_schedule_inputs(self.version)
        frozen = {'version': ScheduleVersionSerializer(self.version).data,
                  'activities': ScheduleActivitySerializer(self.version.activities.all(), many=True).data,
                  'wbs': [], 'relationships': [], 'accepted_inputs': frozen_inputs,
                  'risk_register': [{'name': 'Frozen accepted risk', 'source': 'Reviewed clause'}]}
        ScheduleBaseline.objects.create(schedule=self.schedule, source_version=self.version, name='B0',
                                        approved_by=self.owner, approved_at=timezone.now(), snapshot=frozen)
        self.version.status = 'baselined'
        self.version.save(update_fields=['status', 'updated_at'])
        self.calendar.working_times = {}
        self.calendar.name = 'Changed after baseline'
        self.calendar.save(update_fields=['working_times', 'name', 'updated_at'])
        self.project.name = 'Changed project name'
        self.project.save(update_fields=['name', 'updated_at'])
        response = self.client.get(self.url + 'export/', {'export_format': 'mspdi_zip'})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertEqual(response['X-RADAI-Export-State'], 'approved_baseline')
        with ZipFile(BytesIO(response.content)) as archive:
            provenance = json.loads(archive.read('radai-provenance.json'))['snapshot']
            self.assertEqual(provenance['project']['name'], 'FEED Schedule')
            self.assertEqual(provenance['calendar']['working_times']['0'][0]['from'], '08:00:00')
            self.assertEqual(provenance['risk_register'], frozen['risk_register'])
            self.assertEqual(provenance['traceability']['sha256'], frozen_inputs['sha256'])
            self.assertIn(b'<Baseline>', archive.read('schedule.xml'))
