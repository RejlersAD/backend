"""Run with: python -m unittest discover -s scripts/tests -p test_file_replica_sync.py"""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid


SPEC = importlib.util.spec_from_file_location(
    "file_replica_sync", Path(__file__).resolve().parents[1] / "file_replica_sync.py"
)
agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = agent
SPEC.loader.exec_module(agent)

SOURCE_ID = str(uuid.uuid4())
SCAN_ID = str(uuid.uuid4())
ENTRY_ID = str(uuid.uuid4())
API_URL = "https://radai.example/api/v1/file-replica/agent/"


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.data = data if data is not None else {}
        self.closed = False

    def json(self):
        return self.data

    def close(self):
        self.closed = True


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "5900738 Project" / "Documents").mkdir(parents=True)
        (self.root / "5900738 Project" / "Documents" / "progress.txt").write_text("progress=42")
        (self.root / "5900738 Project" / "Private").mkdir()
        (self.root / "5900738 Project" / "Private" / "payroll.txt").write_text("restricted")
        (self.root / "Another project").mkdir()
        (self.root / "root-file.txt").write_text("not a project folder")

    def inventory(self, **kwargs):
        return agent.Inventory(agent.Configuration(root=self.root, **kwargs))

    def test_empty_scope_discovers_only_immediate_directories(self):
        inventory = self.inventory()
        entries = list(inventory.entries())
        self.assertEqual({entry["relative_path"] for entry in entries}, {"5900738 Project", "Another project"})
        self.assertTrue(all(entry["is_directory"] for entry in entries))
        self.assertEqual(inventory.errors, [])

    def test_explicit_scope_recurses_excludes_and_deduplicates(self):
        inventory = self.inventory(
            included_paths=("5900738 Project", "5900738 Project/Documents"),
            excluded_paths=("5900738 Project/Private",), mode="mirror",
        )
        entries = list(inventory.entries())
        self.assertEqual(len(entries), 3)
        file_entry = next(entry for entry in entries if not entry["is_directory"])
        self.assertEqual(file_entry["checksum"], hashlib.sha256(b"progress=42").hexdigest())
        self.assertEqual(file_entry["parent_path"], "5900738 Project/Documents")
        self.assertEqual(inventory.errors, [])

    def test_unsafe_relative_paths_rejected(self):
        for value in ("../secret", "project/../../secret", "/secret", "C:\\secret", "\\\\server\\share", "project/file:ads", "project//file", "project/.", "project/NUL.txt", "project/file ", "project/a\nfile", "project/*", "project/a\"b"):
            with self.subTest(value=value), self.assertRaises(agent.ReplicaError):
                agent.relative_path(value)
        self.assertEqual(agent.relative_path("5900738 Project\\Documents"), "5900738 Project/Documents")

    def test_nested_selection_emits_ancestors_without_sibling_contents(self):
        inventory = self.inventory(included_paths=("5900738 Project/Documents",))
        paths = {item["relative_path"] for item in inventory.entries()}
        self.assertEqual(paths, {
            "5900738 Project", "5900738 Project/Documents",
            "5900738 Project/Documents/progress.txt",
        })

    def test_nested_then_parent_selection_still_expands_parent(self):
        inventory = self.inventory(included_paths=("5900738 Project/Documents", "5900738 Project"))
        paths = [item["relative_path"] for item in inventory.entries()]
        self.assertEqual(len(paths), 5)
        self.assertEqual(len(set(paths)), 5)
        self.assertIn("5900738 Project/Private/payroll.txt", paths)

    def test_all_project_roots_and_children_precede_deeper_descendants(self):
        later = self.root / "Another project"
        (later / "first-level.txt").write_text("later project is ready to browse")
        (later / "Drawings").mkdir()
        (later / "Drawings" / "drawing.dwg").write_text("synthetic")
        inventory = self.inventory(included_paths=("5900738 Project", "Another project"))
        paths = [item["relative_path"] for item in inventory.entries()]
        self.assertEqual(paths[:2], ["5900738 Project", "Another project"])
        immediate = [index for index, path in enumerate(paths) if path.count("/") == 1]
        deeper = [index for index, path in enumerate(paths) if path.count("/") > 1]
        self.assertLess(max(immediate), min(deeper))
        self.assertIn("Another project/first-level.txt", paths)
        self.assertIn("Another project/Drawings/drawing.dwg", paths)
        self.assertEqual(inventory.errors, [])

    def test_nested_overlapping_includes_are_streamed_once_across_projects(self):
        (self.root / "Another project" / "visible.txt").write_text("synthetic")
        inventory = self.inventory(included_paths=(
            "5900738 Project/Documents", "5900738 Project", "Another project",
            "5900738 Project/Documents", "Another project",
        ))
        with patch.object(inventory, "entry", wraps=inventory.entry) as read_entry:
            stream = inventory.entries()
            roots = [next(stream)["relative_path"], next(stream)["relative_path"]]
            self.assertEqual(roots, ["5900738 Project", "Another project"])
            self.assertEqual(read_entry.call_count, 2)
            paths = roots + [item["relative_path"] for item in stream]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(read_entry.call_count, len(paths))
        self.assertLess(paths.index("Another project/visible.txt"), paths.index("5900738 Project/Documents/progress.txt"))
        self.assertEqual(inventory.errors, [])

    def test_unreadable_project_does_not_prevent_later_project_inventory(self):
        (self.root / "Another project" / "visible.txt").write_text("synthetic")
        inventory = self.inventory(included_paths=("5900738 Project", "Another project"))
        original = agent.os.scandir
        def scan(path):
            if Path(path) == self.root / "5900738 Project":
                raise PermissionError("synthetic inaccessible directory")
            return original(path)
        with patch.object(agent.os, "scandir", side_effect=scan):
            paths = [item["relative_path"] for item in inventory.entries()]
        self.assertEqual(paths, ["5900738 Project", "Another project", "Another project/visible.txt"])
        self.assertTrue(inventory.errors)

    def test_symlink_cannot_escape_scope(self):
        target = self.root / "5900738 Project" / "Documents" / "link"
        try:
            target.symlink_to(self.root / "root-file.txt")
        except OSError:
            self.skipTest("Creating symlinks requires permission on this Windows host")
        inventory = self.inventory(included_paths=("5900738 Project/Documents",))
        entries = list(inventory.entries())
        self.assertNotIn("5900738 Project/Documents/link", {entry["relative_path"] for entry in entries})
        self.assertTrue(inventory.errors)

    def test_windows_reparse_attribute_is_rejected(self):
        info = Mock(st_mode=0, st_file_attributes=agent.REPARSE_POINT)
        self.assertTrue(agent.is_reparse(info))

    def test_unavailable_included_folder_prevents_success(self):
        inventory = self.inventory(included_paths=("missing-project",))
        self.assertEqual(list(inventory.entries()), [])
        self.assertTrue(inventory.errors)

    def test_oversized_mirror_file_is_recorded_as_error(self):
        large = self.root / "Another project" / "large.dat"
        with large.open("wb") as handle:
            handle.truncate(1024 * 1024 + 1)
        inventory = self.inventory(included_paths=("Another project",), mode="mirror", max_file_size_mb=1)
        entry = list(inventory.entries())[-1]
        self.assertEqual(entry["checksum"], "")
        self.assertIn("size limit", entry["error"])
        self.assertTrue(inventory.errors)

    def test_recursive_catalogue_includes_every_file_type_without_reading_content(self):
        deep = self.root / 'Another project' / 'Engineering' / 'Issued' / 'Vendor'
        deep.mkdir(parents=True)
        names = ['drawing.PDF', 'model.dwg', 'package.zip', 'installer.exe', 'README', 'vendor.xyz']
        for name in names:
            (deep / name).write_bytes(b'synthetic')
        with (deep / 'large.bin').open('wb') as content:
            content.truncate(2 * 1024 * 1024)
        inventory = self.inventory(included_paths=('Another project',), mode='catalogue', max_file_size_mb=1)
        with patch.object(inventory, 'open_source', side_effect=AssertionError('Catalogue must not read bytes')):
            entries = list(inventory.entries())
        files = [entry for entry in entries if not entry['is_directory']]
        self.assertEqual({entry['name'] for entry in files}, set(names + ['large.bin']))
        self.assertTrue(all(not entry['checksum'] and not entry['error'] for entry in files))
        self.assertEqual(inventory.errors, [])

    def test_invalid_child_does_not_hide_valid_siblings(self):
        inventory = self.inventory(included_paths=('Another project',))
        valid = self.root / 'Another project' / 'valid.txt'
        valid.write_text('synthetic')
        original = agent.os.scandir
        def scan(path):
            if Path(path) == valid.parent:
                manager = Mock()
                manager.__enter__ = Mock(return_value=iter([Mock(name='invalid'), Mock(name='valid')]))
                manager.__exit__ = Mock(return_value=False)
                invalid_child = Mock(); invalid_child.name = 'unsupported:name'
                valid_child = Mock(); valid_child.name = valid.name
                manager.__enter__.return_value = iter([invalid_child, valid_child])
                return manager
            return original(path)
        with patch.object(agent.os, 'scandir', side_effect=scan):
            entries = list(inventory.entries())
        self.assertIn('Another project/valid.txt', [entry['relative_path'] for entry in entries])
        self.assertEqual(len(inventory.errors), 1)

    def test_case_insensitive_exclusion_preserves_similarly_named_sibling(self):
        sibling = self.root / '5900738 Project' / 'Private2'
        sibling.mkdir()
        (sibling / 'allowed.txt').write_text('synthetic')
        inventory = self.inventory(included_paths=('5900738 Project',), excluded_paths=('5900738 project/private',))
        paths = [entry['relative_path'] for entry in inventory.entries()]
        self.assertNotIn('5900738 Project/Private/payroll.txt', paths)
        self.assertIn('5900738 Project/Private2/allowed.txt', paths)

    def test_modification_during_read_fails_stability_check(self):
        inventory = self.inventory(mode="mirror")
        name = "5900738 Project/Documents/progress.txt"
        with self.assertRaises(agent.ReplicaError):
            with inventory.open_source(name) as (source, before):
                source.read()
                (self.root / name).write_text("progress has changed to a different size")

    def test_dry_run_requires_no_api_credentials_or_dependency(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(agent, "API") as api:
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                status = agent.main(["--dry-run", "--root", str(self.root)])
            self.assertEqual(status, 0)
            api.assert_not_called()
            self.assertEqual(len(output.getvalue().splitlines()), 2)


class APITests(unittest.TestCase):
    def client(self, replies):
        session = Mock()
        session.headers = {}
        session.request.side_effect = replies
        return agent.API(API_URL, SOURCE_ID, "secret-token", session=session), session

    def test_url_validation_rejects_insecure_remote_or_credentials(self):
        for url in (
            "http://office.example/api/v1/file-replica/agent/",
            "https://user:pass@radai.example/api/v1/file-replica/agent/",
            API_URL + "?token=secret", API_URL + "#fragment",
            "https://radai.example/arbitrary/", "file:///api/v1/file-replica/agent/",
        ):
            with self.subTest(url=url), self.assertRaises(agent.ReplicaError):
                agent.validate_api_url(url)
        self.assertEqual(agent.validate_api_url("http://127.0.0.1:8000/api/v1/file-replica/agent"), "http://127.0.0.1:8000/api/v1/file-replica/agent/")

    def test_retries_same_scan_body_and_rejects_redirects(self):
        client, session = self.client([FakeResponse(503), FakeResponse(data={"id": SCAN_ID})])
        payload = {"run_id": str(uuid.uuid4())}
        with patch.object(agent.time, "sleep"):
            result = client.request("POST", "scans/", json=payload)
        self.assertEqual(result["id"], SCAN_ID)
        self.assertEqual(session.request.call_count, 2)
        for call in session.request.call_args_list:
            self.assertEqual(call.kwargs["json"], payload)
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertEqual(call.kwargs["timeout"], (15, 120))
        client, session = self.client([FakeResponse(302)])
        with self.assertRaisesRegex(agent.ReplicaError, "HTTP 302"):
            client.request("GET", "config/")
        self.assertEqual(session.request.call_count, 1)

    def test_connection_error_does_not_disclose_token(self):
        client, session = self.client([RuntimeError("https://secret-token@host")] * 3)
        with patch.object(agent.time, "sleep"), self.assertRaises(agent.ReplicaError) as caught:
            client.request("GET", "config/")
        self.assertNotIn("secret-token", str(caught.exception))

    def test_endpoint_rejects_server_supplied_url(self):
        client, session = self.client([])
        with self.assertRaises(agent.ReplicaError):
            client.request("POST", "https://other.example/upload")
        session.request.assert_not_called()

    def test_upload_is_stable_snapshot_with_rewound_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_bytes(b"drawing revision 1")
            inventory = agent.Inventory(agent.Configuration(root=root, mode="mirror"))
            entry = inventory.entry("file.txt")
            client, session = self.client([])
            bodies = []

            def receive(method, url, **kwargs):
                body = kwargs["data"].read()
                self.assertEqual(len(body), int(kwargs["headers"]["Content-Length"]))
                bodies.append(body)
                return FakeResponse(503 if len(bodies) == 1 else 200, {"id": ENTRY_ID, "status": "available"})

            session.request.side_effect = receive
            with patch.object(agent.time, "sleep"):
                client.upload(inventory, entry, ENTRY_ID, SCAN_ID)
            self.assertEqual(bodies[0], bodies[1])
            self.assertIn(b"drawing revision 1", bodies[0])
            self.assertIn(entry["checksum"].encode(), bodies[0])

    def test_file_changed_after_inventory_never_uploads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "file.txt"
            path.write_bytes(b"old")
            inventory = agent.Inventory(agent.Configuration(root=root, mode="mirror"))
            entry = inventory.entry("file.txt")
            path.write_bytes(b"new content")
            client, session = self.client([])
            with self.assertRaisesRegex(agent.ReplicaError, "changed after inventory"):
                client.upload(inventory, entry, ENTRY_ID, SCAN_ID)
            session.request.assert_not_called()


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "project").mkdir()
        (self.root / "project" / "report.txt").write_text("data")
        self.config = agent.Configuration(root=self.root, included_paths=("project",), mode="mirror")

    def client(self, upload_required=True):
        client = Mock()
        calls = []

        def request(method, endpoint, **kwargs):
            calls.append((endpoint, kwargs.get("json")))
            if endpoint == "scans/":
                return {"id": SCAN_ID, "status": "running"}
            if endpoint.endswith("/entries/"):
                return {"entries": [
                    {"id": ENTRY_ID, "relative_path": entry["relative_path"],
                     "upload_required": upload_required and not entry["is_directory"], "status": "pending"}
                    for entry in kwargs["json"]["entries"]
                ]}
            return {"id": SCAN_ID, "status": "completed"}

        client.request.side_effect = request
        return client, calls

    def test_inventory_always_sent_upload_only_when_requested(self):
        client, calls = self.client(upload_required=False)
        self.assertTrue(agent.synchronize(client, self.config))
        self.assertTrue(agent.synchronize(client, self.config))
        client.upload.assert_not_called()
        self.assertEqual(sum(endpoint.endswith("/entries/") for endpoint, _ in calls), 2)
        self.assertTrue(calls[-1][1]["success"])

    def test_catalogue_streams_full_and_partial_batches(self):
        for index in range(204):
            (self.root / 'project' / f'file-{index}.xyz').write_text('synthetic')
        client, calls = self.client(upload_required=False)
        config = agent.Configuration(root=self.root, included_paths=('project',), mode='catalogue')
        self.assertTrue(agent.synchronize(client, config))
        batches = [body['entries'] for endpoint, body in calls if endpoint.endswith('/entries/')]
        self.assertEqual([len(batch) for batch in batches], [100, 100, 6])
        self.assertEqual(len({row['relative_path'] for batch in batches for row in batch}), 206)
        client.upload.assert_not_called()

    def test_upload_failure_cannot_mark_scan_successful(self):
        client, calls = self.client()
        client.upload.side_effect = agent.ReplicaError("Upload interrupted")
        self.assertFalse(agent.synchronize(client, self.config))
        self.assertFalse(calls[-1][1]["success"])
        self.assertIn("Upload interrupted", calls[-1][1]["error"])

    def test_root_failure_records_failed_completion(self):
        client, calls = self.client()
        config = agent.Configuration(root=self.root / "unavailable")
        self.assertFalse(agent.synchronize(client, config))
        self.assertFalse(calls[-1][1]["success"])
        self.assertEqual(len(calls), 2)

    def test_permission_failure_records_failed_completion(self):
        client, calls = self.client()
        original = agent.os.scandir

        def read_directory(path):
            if Path(path).name == "project":
                raise PermissionError("denied")
            return original(path)

        with patch.object(agent.os, "scandir", side_effect=read_directory):
            self.assertFalse(agent.synchronize(client, self.config))
        self.assertFalse(calls[-1][1]["success"])

    def test_unacknowledged_entries_prevent_success(self):
        client, calls = self.client()
        original = client.request.side_effect

        def request(method, endpoint, **kwargs):
            data = original(method, endpoint, **kwargs)
            if endpoint.endswith("/entries/"):
                data["entries"] = []
            return data

        client.request.side_effect = request
        self.assertFalse(agent.synchronize(client, self.config))
        self.assertFalse(calls[-1][1]["success"])

    def test_disabled_source_does_not_start_scan(self):
        client, calls = self.client()
        config = agent.Configuration(root=self.root, enabled=False)
        self.assertTrue(agent.synchronize(client, config))
        self.assertEqual(calls, [])

    def test_backend_failed_completion_is_not_reported_as_success(self):
        client, calls = self.client(upload_required=False)
        original = client.request.side_effect

        def request(method, endpoint, **kwargs):
            response = original(method, endpoint, **kwargs)
            if endpoint.endswith("/complete/"):
                response["status"] = "failed"
            return response

        client.request.side_effect = request
        self.assertFalse(agent.synchronize(client, self.config))


class HeartbeatTests(unittest.TestCase):
    def test_heartbeat_uses_separate_session_and_bounded_network_timeout(self):
        api = Mock()
        heartbeat = agent.ScanHeartbeat(api, SCAN_ID)
        heartbeat.stopped = Mock()
        heartbeat.stopped.wait.side_effect = [False, True]
        heartbeat.run()
        client = api.heartbeat_client.return_value
        client.request.assert_called_once_with('POST', f'scans/{SCAN_ID}/heartbeat/', json={}, timeout=(5, 15), attempts=1)
        client.session.close.assert_called_once()
        api.request.assert_not_called()

    def test_heartbeat_connection_failure_does_not_abort_inventory(self):
        api = Mock()
        api.heartbeat_client.return_value.request.side_effect = agent.ReplicaError('synthetic failure')
        heartbeat = agent.ScanHeartbeat(api, SCAN_ID)
        heartbeat.stopped = Mock()
        heartbeat.stopped.is_set.return_value = False
        heartbeat.stopped.wait.side_effect = [False, False, True]
        with self.assertLogs(agent.LOG, level='WARNING') as messages:
            heartbeat.run()
        self.assertEqual(len(messages.records), 2)
        self.assertEqual(api.heartbeat_client.return_value.request.call_count, 2)
        api.heartbeat_client.return_value.session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
