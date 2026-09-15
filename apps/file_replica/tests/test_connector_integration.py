"""Actual office connector HTTP requests against an isolated Django live server.

Every source file and copied byte is synthetic and stored in temporary folders.
Run with config.settings_file_replica_test; no office or production access occurs.
"""
import hashlib
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage
from django.test import LiveServerTestCase
import requests

from apps.core.project_models import Project
from apps.file_replica.models import ReplicaEntry, ReplicaExtraction, ReplicaSource, ReplicaVersion
from apps.file_replica.serializers import ExtractionSerializer
from scripts import file_replica_sync as connector


class ConnectorIntegrationTests(LiveServerTestCase):
    # Windows can resolve localhost to ::1 while Django binds only IPv4, causing
    # a connection timeout for every request before address fallback.
    host = "127.0.0.1"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="radai-replica-integration-")
        self.addCleanup(self.temporary.cleanup)
        temporary_root = Path(self.temporary.name)
        self.source_root = temporary_root / "source"
        self.project_folder = "9900001 Synthetic Project"
        self.selected_path = self.project_folder + "/Documents"
        selected = self.source_root / self.selected_path
        selected.mkdir(parents=True)
        self.document = selected / "progress.txt"
        self.original_content = b"Milestone: synthetic pilot\nProgress: 25%\n"
        self.document.write_bytes(self.original_content)
        (selected / "Private").mkdir()
        (selected / "Private" / "excluded.txt").write_text("synthetic excluded content")
        (self.source_root / self.project_folder / "unselected.txt").write_text("synthetic unselected sibling")
        self.storage_patch = patch.object(
            ReplicaVersion._meta.get_field("file"), "storage",
            FileSystemStorage(location=temporary_root / "replica-storage"),
        )
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.project = Project.objects.create(code="9900001", name="Synthetic connector pilot")
        self.token = "synthetic-integration-token"
        self.source = ReplicaSource.objects.create(
            name="Synthetic integration source", root_path=str(self.source_root),
            included_paths=[self.selected_path],
            excluded_paths=[self.selected_path + "/Private"], mode="mirror", enabled=True,
            token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
        )
        self.session = requests.Session()
        # Keep loopback integration independent of workstation proxy settings.
        self.session.trust_env = False
        self.addCleanup(self.session.close)
        self.api = connector.API(
            self.live_server_url + "/api/v1/file-replica/agent/",
            str(self.source.id), self.token, session=self.session,
        )

    def run_scan(self):
        config = connector.Configuration.from_api(self.api.request("GET", "config/"))
        return connector.synchronize(self.api, config)

    def file_entry(self):
        return ReplicaEntry.objects.get(
            source=self.source, relative_path=self.selected_path + "/progress.txt",
        )

    def assert_stored_content(self, version, expected):
        with version.file.open("rb") as content:
            self.assertEqual(content.read(), expected)

    def test_real_http_retry_unchanged_changed_and_removed_document(self):
        # The first content POST reaches Django and commits, then the client loses
        # the response. Retrying the exact snapshot must not create version 2.
        real_request = self.session.request
        dropped = []

        def lose_first_upload_response(method, url, **kwargs):
            response = real_request(method, url, **kwargs)
            if method == "POST" and url.endswith("/content/") and not dropped:
                self.assertEqual(response.status_code, 201)
                dropped.append(True)
                response.close()
                raise requests.ConnectionError("Synthetic lost response after commit")
            return response

        with patch.object(self.session, "request", side_effect=lose_first_upload_response):
            self.assertTrue(self.run_scan())
        self.assertEqual(dropped, [True])
        entry = self.file_entry()
        self.assertEqual(entry.status, "available")
        self.assertEqual(entry.name, "progress.txt")
        self.assertEqual(entry.content_type, "text/plain")
        self.assertEqual(entry.checksum, hashlib.sha256(self.original_content).hexdigest())
        self.assertEqual(entry.versions.count(), 1)
        self.assertEqual(entry.scope.project_id, self.project.id)
        self.assertFalse(entry.scope.access_enabled)
        self.assert_stored_content(entry.current_version, self.original_content)
        self.assertEqual(set(self.source.entries.values_list("relative_path", flat=True)), {
            self.project_folder, self.selected_path, self.selected_path + "/progress.txt",
        })
        old_version = entry.current_version
        extraction = ReplicaExtraction.objects.create(entry=entry, version=old_version)
        self.assertFalse(ExtractionSerializer(extraction).data["stale"])

        # Inventory is resent, but unchanged bytes do not transfer or version again.
        with patch.object(self.session, "request", wraps=real_request) as recorded:
            self.assertTrue(self.run_scan())
        self.assertFalse(any(call.args[1].endswith("/content/") for call in recorded.call_args_list))
        entry.refresh_from_db()
        self.assertEqual(entry.versions.count(), 1)

        updated = b"Milestone: synthetic pilot updated\nProgress: 70%\n"
        self.document.write_bytes(updated)
        self.assertTrue(self.run_scan())
        entry.refresh_from_db()
        self.assertEqual(entry.current_version.number, 2)
        self.assertEqual(entry.versions.count(), 2)
        self.assert_stored_content(entry.current_version, updated)
        self.assert_stored_content(old_version, self.original_content)
        extraction.refresh_from_db()
        self.assertTrue(ExtractionSerializer(extraction).data["stale"])

        self.document.unlink()
        self.assertTrue(self.run_scan())
        entry.refresh_from_db()
        self.assertEqual(entry.status, "missing")
        self.assertEqual(entry.versions.count(), 2)
        self.assert_stored_content(entry.current_version, updated)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.active_run)
        self.assertIsNotNone(self.source.last_success_at)
        self.assertEqual(self.source.scans.filter(status="completed").count(), 4)

    def test_unavailable_share_fails_without_marking_prior_files_missing(self):
        self.assertTrue(self.run_scan())
        entry = self.file_entry()
        version_id = entry.current_version_id
        last_success = self.source.scans.get(status="completed").completed_at

        # Move only this test's synthetic source root to simulate an offline share.
        offline_root = self.source_root.with_name("source-offline")
        temporary_root = Path(self.temporary.name).resolve()
        self.assertTrue(self.source_root.resolve().is_relative_to(temporary_root))
        self.assertTrue(offline_root.resolve().is_relative_to(temporary_root))
        self.source_root.rename(offline_root)
        self.assertFalse(self.run_scan())
        entry.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(entry.status, "available")
        self.assertEqual(entry.current_version_id, version_id)
        self.assert_stored_content(entry.current_version, self.original_content)
        self.assertEqual(self.source.last_success_at, last_success)
        self.assertEqual(self.source.scans.first().status, "failed")
        self.assertIsNone(self.source.active_run)
