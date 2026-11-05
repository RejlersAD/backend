import copy
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from apps.process_datasheet.services.separator_sizing import INPUTS, VERSION
from apps.process_datasheet.services.separator_snapshots import MAX_BYTES, build_snapshot, decode_snapshot
from apps.process_datasheet.tests.test_separator_sizing import example_payload


def example_basis():
    request = example_payload()
    sources = [{
        "id": case["id"], "name": case["name"], "fileName": f"case-{case['id']}.xlsx",
        "sheetName": "Streams", "streamName": "1032",
        "mapping": {key: {"value": val, "source": {
            "property": INPUTS[key][0], "section": "", "unit": INPUTS[key][1],
            "value": val, "edited": False,
        }} for key, val in case["inputs"].items()},
    } for case in request["cases"]]
    return {"request": request, "sources": sources, "excluded": [], "overrides": {},
            "metadata": {"tag": "V-1001", "service": "Separator", "stream": "1032"}}


class SnapshotBasisTests(unittest.TestCase):
    def test_document_metadata_round_trip_and_validation(self):
        basis = example_basis()
        basis["metadata"]["document"] = {"project": "Project A", "client": "Client A", "revision": "B", "documentNo": "DS-1", "quantity": "1"}
        _, content = build_snapshot(basis, VERSION, "2026-11-05")
        self.assertEqual(decode_snapshot(content)["basis"]["metadata"]["document"], basis["metadata"]["document"])
        basis["metadata"]["document"]["quantity"] = "invalid"
        with self.assertRaisesRegex(ValueError, "quantity"):
            build_snapshot(basis, VERSION, "2026-11-05")

    def test_actual_database_and_private_file_round_trip_in_isolated_process(self):
        code = '''
import json, os, sys, tempfile
os.environ["HMB_ACCESS_STRATEGY"] = "owner"
from django.apps import AppConfig
class QuietProcessConfig(AppConfig):
    name = "apps.process_datasheet"
from django.conf import settings
with tempfile.TemporaryDirectory() as directory:
    settings.configure(SECRET_KEY="separator-isolated-test", DEBUG=True, USE_S3=False,
        BASE_DIR=directory, USE_I18N=False,
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes",
                        "apps.project_organizer", "__main__.QuietProcessConfig"],
        DATABASES={"default":{"ENGINE":"django.db.backends.sqlite3","NAME":":memory:"}},
        REST_FRAMEWORK={"UNAUTHENTICATED_USER":None,"DEFAULT_AUTHENTICATION_CLASSES":[]})
    import django
    django.setup()
    from django.db import models, connection
    # Resolve the optional, non-constrained enterprise FK without loading unrelated apps.
    type("Project", (models.Model,), {"__module__":__name__,
        "Meta":type("Meta", (), {"app_label":"core"})})
    from django.contrib.auth.models import User, Group, Permission
    from django.contrib.contenttypes.models import ContentType
    from apps.project_organizer.models import Project
    from apps.process_datasheet.models import HMBMasterTemplateProfile, HMBCaseImportBatch, HMBSourceUpload
    with connection.schema_editor() as editor:
        for model in (ContentType, Permission, Group, User, Project,
                      HMBMasterTemplateProfile, HMBCaseImportBatch, HMBSourceUpload):
            editor.create_model(model)
    from rest_framework.test import APIRequestFactory, force_authenticate
    from unittest.mock import patch
    from apps.process_datasheet.separator_snapshot_view import separator_snapshots_view, reopen_separator_snapshot_view
    from apps.process_datasheet.services.separator_snapshots import without_separator_snapshots
    from apps.process_datasheet.services.separator_sizing import VERSION
    owner = User.objects.create(username="snapshot-owner")
    intruder = User.objects.create(username="snapshot-other")
    project = Project.objects.create(name="Snapshot test", created_by=owner)
    other = Project.objects.create(name="Other test", created_by=intruder)
    factory = APIRequestFactory()
    basis = json.load(sys.stdin)
    def request(method, user, data, upload=None):
        req = getattr(factory, method)("/", data, format="json")
        force_authenticate(req, user=user)
        with patch("apps.process_datasheet.separator_snapshot_view.user_has_module_access", return_value=True):
            return reopen_separator_snapshot_view(req, upload_id=upload) if upload else separator_snapshots_view(req)
    response = request("post", owner, {"project_id":str(project.pk),"calculator_version":VERSION,"basis":basis})
    assert response.status_code == 201, response.data
    source = HMBSourceUpload.objects.get(pk=response.data["snapshot"]["id"])
    assert source.project_id == project.pk
    listing = request("get", owner, {"project_id":str(project.pk)})
    assert len(listing.data["results"]) == 1, listing.data
    reopened = request("get", owner, {"project_id":str(project.pk)}, source.pk)
    assert reopened.status_code == 200 and reopened.data["snapshot"]["basis"] == basis, reopened.data
    assert request("get", intruder, {"project_id":str(project.pk)}, source.pk).status_code == 403
    assert request("post", intruder, {"project_id":str(project.pk),"calculator_version":VERSION,"basis":basis}).status_code == 403
    assert request("get", intruder, {"project_id":str(other.pk)}, source.pk).status_code == 404
    assert HMBSourceUpload.objects.count() == 1
    for index, metadata in enumerate(({}, {"export_kind":"comparison_workbook"})):
        HMBSourceUpload.objects.create(project=project, uploaded_by=owner, upload_kind="output_template",
            original_filename="legacy.xlsx", storage_key=f"legacy-{index}", file_sha256="0"*64,
            size_bytes=0, metadata=metadata)
    assert without_separator_snapshots(HMBSourceUpload.objects.all()).count() == 2
    print("Actual ORM, transactions, private-file round-trip, owner permissions and legacy JSON filters passed.")
'''
        completed = subprocess.run([sys.executable, "-c", code], input=json.dumps(example_basis()),
                                   text=True, capture_output=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_round_trip_preserves_basis_and_server_results(self):
        basis = example_basis()
        basis["overrides"]["0:oil_flow"] = "201.5"
        basis["request"]["cases"][0]["inputs"]["oil_flow"] = 201.5
        basis["excluded"] = ["2"]
        basis["request"]["cases"] = basis["request"]["cases"][:2]
        original = copy.deepcopy(basis)
        snapshot, content = build_snapshot(basis, VERSION, "2026-11-05T00:00:00Z")
        reopened = decode_snapshot(content)
        self.assertEqual(basis, original)
        self.assertEqual(reopened["basis"], basis)
        self.assertEqual(snapshot["result"]["cases"][0]["inputs"]["oil_flow"], 201.5)
        self.assertEqual(snapshot["result"]["status"], "REVIEW")

    def test_inconsistent_provenance_or_version_is_rejected(self):
        basis = example_basis()
        basis["request"]["cases"][0]["inputs"]["oil_flow"] = 201
        with self.assertRaisesRegex(ValueError, "provenance"):
            build_snapshot(basis, VERSION, "2026-11-05")
        with self.assertRaisesRegex(ValueError, "version"):
            build_snapshot(example_basis(), "old-version", "2026-11-05")

    def test_invalid_formats_and_nonfinite_values_are_rejected(self):
        for content in (b"{}", b"not JSON", b'{"format":"radai.separator.snapshot","format_version":1,"result":NaN}'):
            with self.assertRaises(ValueError):
                decode_snapshot(content)
        basis = example_basis()
        basis["sources"][0]["mapping"]["oil_flow"]["value"] = float("inf")
        with self.assertRaises(ValueError):
            build_snapshot(basis, VERSION, "2026-11-05")

    def test_snapshot_size_limit_exact_boundary(self):
        _, content = build_snapshot(example_basis(), VERSION, "2026-11-05")
        padded = content + b" " * (MAX_BYTES - len(content))
        self.assertEqual(len(padded), MAX_BYTES)
        self.assertEqual(decode_snapshot(padded)["format_version"], 1)
        with self.assertRaisesRegex(ValueError, "2 MB"):
            decode_snapshot(padded + b" ")
        basis = example_basis()
        basis["sources"][0]["mapping"]["gas_flow"]["source"]["value"] = "x" * MAX_BYTES
        with self.assertRaisesRegex(ValueError, "2 MB"):
            build_snapshot(basis, VERSION, "2026-11-05")

    def test_historical_decode_never_recalculates(self):
        snapshot, _ = build_snapshot(example_basis(), VERSION, "2026-11-05")
        snapshot["calculator_version"] = snapshot["result"]["version"] = snapshot["schema"]["version"] = "separator-old"
        with patch("apps.process_datasheet.services.separator_snapshots.calculate", side_effect=AssertionError("Do not recalculate historical results")):
            self.assertEqual(decode_snapshot(json.dumps(snapshot).encode())["calculator_version"], "separator-old")


class SnapshotAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from django.conf import settings
        if not settings.configured:
            settings.configure(SECRET_KEY="separator-test-only", USE_I18N=False, REST_FRAMEWORK={
                "UNAUTHENTICATED_USER": None, "DEFAULT_AUTHENTICATION_CLASSES": [],
            })
        from rest_framework.test import APIRequestFactory
        from apps.process_datasheet.separator_snapshot_view import separator_snapshots_view, reopen_separator_snapshot_view
        cls.factory = APIRequestFactory()
        cls.archive_view = staticmethod(separator_snapshots_view)
        cls.reopen_view = staticmethod(reopen_separator_snapshot_view)

    def setUp(self):
        from django.test import override_settings
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(BASE_DIR=self.directory.name, USE_S3=False, DEBUG=True)
        settings.enable()
        self.addCleanup(settings.disable)
        self.project = SimpleNamespace(project_id=uuid.uuid4())
        self.user = SimpleNamespace(id=1, is_authenticated=True, is_superuser=False)
        self.records = {}
        self.manager = MagicMock()
        self.model = SimpleNamespace(KIND_OUTPUT_TEMPLATE="output_template", STATUS_IMPORTED="imported",
                                     objects=self.manager, DoesNotExist=LookupError)
        self.manager.create.side_effect = self.create_record
        self.manager.select_related.return_value.get.side_effect = self.get_record
        self.manager.filter.side_effect = self.filter_records
        for target, kwargs in [
            ("source_model", {"return_value": self.model}),
            ("accessible_project", {"return_value": (self.project, None)}),
            ("user_has_module_access", {"return_value": True}),
            ("transaction.atomic", {"side_effect": nullcontext}),
        ]:
            mock = patch(f"apps.process_datasheet.separator_snapshot_view.{target}", **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def create_record(self, **values):
        from django.utils import timezone
        record = SimpleNamespace(id=uuid.uuid4(), created_at=timezone.now(), **values)
        self.records[str(record.id)] = record
        return record

    def get_record(self, **filters):
        record = self.records.get(str(filters["pk"]))
        if record is None or record.project != filters["project"] or any(
                record.metadata[key.split("__")[1]] != val for key, val in filters.items() if key.startswith("metadata__")):
            raise LookupError
        return record

    def filter_records(self, **filters):
        records = [record for record in self.records.values() if record.project == filters["project"]
                   and record.metadata["source_tool"] == filters["metadata__source_tool"]
                   and record.metadata["export_kind"] == filters["metadata__export_kind"]]
        queryset = MagicMock()
        queryset.select_related.return_value.order_by.return_value = list(reversed(records))
        return queryset

    def request(self, method, data=None, upload_id=None, authenticated=True):
        from rest_framework.test import force_authenticate
        request = getattr(self.factory, method)("/", data or {}, format="json")
        if authenticated:
            force_authenticate(request, user=self.user)
        return self.reopen_view(request, upload_id=upload_id) if upload_id else self.archive_view(request)

    def save(self):
        return self.request("post", {"project_id": str(self.project.project_id),
                                    "calculator_version": VERSION, "basis": example_basis()})

    def test_private_file_save_list_and_reopen_round_trip(self):
        response = self.save()
        self.assertEqual(response.status_code, 201)
        record = self.records[response.data["snapshot"]["id"]]
        self.assertEqual(record.content_type, "application/json")
        self.assertTrue(record.storage_key.endswith(".json"))
        listing = self.request("get", {"project_id": str(self.project.project_id)})
        self.assertEqual(len(listing.data["results"]), 1)
        reopened = self.request("get", {"project_id": str(self.project.project_id)}, upload_id=record.id)
        self.assertEqual(reopened.status_code, 200)
        self.assertTrue(reopened.data["historical"])
        self.assertEqual(reopened.data["snapshot"]["basis"], example_basis())
        self.assertEqual(reopened.data["snapshot"]["result"]["governing_case"], "Max Water")

    def test_project_required_and_unauthenticated_denied(self):
        self.assertEqual(self.request("post", {"basis": example_basis()}).status_code, 400)
        self.assertEqual(self.request("post", [{"project_id": str(self.project.project_id)}]).status_code, 400)
        self.assertIn(self.request("get", authenticated=False).status_code, (401, 403))
        self.manager.create.assert_not_called()

    def test_denied_project_and_module_access_never_read_or_write(self):
        from rest_framework.response import Response
        with patch("apps.process_datasheet.separator_snapshot_view.accessible_project", return_value=(None, Response({"error": "Access denied"}, status=403))):
            self.assertEqual(self.save().status_code, 403)
        with patch("apps.process_datasheet.separator_snapshot_view.user_has_module_access", return_value=False):
            self.assertEqual(self.save().status_code, 403)
        self.manager.create.assert_not_called()
        self.manager.select_related.assert_not_called()

    def test_other_project_and_comparison_records_are_not_reopened(self):
        self.save()
        record = next(iter(self.records.values()))
        record.project = SimpleNamespace(project_id=uuid.uuid4())
        response = self.request("get", {"project_id": str(self.project.project_id)}, upload_id=record.id)
        self.assertEqual(response.status_code, 404)
        record.project = self.project
        record.metadata["export_kind"] = "comparison_workbook"
        self.assertEqual(self.request("get", {"project_id": str(self.project.project_id)}, upload_id=record.id).status_code, 404)

    def test_database_failure_removes_uploaded_file(self):
        from django.db import DatabaseError
        self.manager.create.side_effect = DatabaseError("test database unavailable")
        self.assertEqual(self.save().status_code, 503)
        self.assertEqual(list(Path(self.directory.name).rglob("*.json")), [])

    def test_private_storage_failure_is_not_success(self):
        from apps.process_datasheet.services.hmb_storage import HMBStorageError
        with patch("apps.process_datasheet.separator_snapshot_view.store_hmb_source", side_effect=HMBStorageError("unavailable")):
            self.assertEqual(self.save().status_code, 503)
        self.manager.create.assert_not_called()

    def test_corrupt_private_file_is_not_restored(self):
        self.save()
        record = next(iter(self.records.values()))
        (Path(self.directory.name) / "private_hmb" / record.storage_key).write_bytes(b"corrupt")
        response = self.request("get", {"project_id": str(self.project.project_id)}, upload_id=record.id)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("snapshot", response.data)

    def test_existing_local_workbook_storage_still_uses_xlsx(self):
        from apps.process_datasheet.services.hmb_storage import store_hmb_source
        path = Path(self.directory.name) / "source.xlsx"
        path.write_bytes(b"workbook")
        stored = store_hmb_source(str(path), upload_kind="output_template", original_filename="source.xlsx",
                                  project_id=self.project.project_id, user_id=1)
        self.assertTrue(stored["key"].endswith(".xlsx"))
        self.assertEqual(stored["content_type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_json_snapshot_s3_content_type_and_private_folder(self):
        from django.test import override_settings
        from apps.process_datasheet.services.hmb_storage import store_hmb_source
        path = Path(self.directory.name) / "snapshot.json"
        path.write_bytes(b"{}")
        with override_settings(USE_S3=True), patch("apps.process_datasheet.services.hmb_storage.S3Service") as service:
            service.return_value.upload_file.return_value = {"success": True, "key": "private/snapshot.json", "size": 2}
            stored = store_hmb_source(str(path), upload_kind="output_template", original_filename="snapshot.json",
                                      project_id=self.project.project_id, user_id=1)
            args, kwargs = service.return_value.upload_file.call_args
            self.assertEqual(args[1], "hmb_output_templates")
            self.assertEqual(kwargs["content_type"], "application/json")
            self.assertEqual(stored["backend"], "s3")
