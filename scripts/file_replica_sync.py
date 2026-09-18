#!/usr/bin/env python3
"""Read an office file share and synchronize its selected folders to RADAI.

The source is always read-only. No API URL or credentials are supplied by default.
Use --dry-run --root <path> --include <project-folder> before connecting a source.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import logging
import os
import re
import stat
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import urlsplit


LOG = logging.getLogger("file-replica-sync")
BATCH_SIZE = 100
CHUNK_SIZE = 1024 * 1024
HEARTBEAT_SECONDS = 60
MAX_FILE_SIZE_MB = 1024
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ReplicaError(RuntimeError):
    """A safe diagnostic suitable for an operator or the scan status."""


def relative_path(value: str) -> str:
    """Normalize source-relative paths without allowing Windows path escapes."""
    if not isinstance(value, str):
        raise ReplicaError("Source paths must be strings")
    value = value.replace("\\", "/")
    parts = value.split("/")
    if not value or len(value) > 2048 or value.startswith("/") or any(
        part in ("", ".", "..") or part.endswith((".", " "))
        or any(ord(char) < 32 or char in ':*?"<>|' for char in part)
        or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
        for part in parts
    ):
        raise ReplicaError("Invalid source-relative path")
    return "/".join(parts)


def is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & REPARSE_POINT
    )


def signature(info: os.stat_result) -> tuple[int, ...]:
    # Windows Python can expose different ctime values through lstat and fstat.
    # Device/inode, size and mtime are comparable across both calls; the content
    # hash is checked again when preparing the upload.
    values = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return values if os.name == "nt" else values + (info.st_ctime_ns,)


@dataclass(frozen=True)
class Configuration:
    root: Path
    included_paths: tuple[str, ...] = ()
    excluded_paths: tuple[str, ...] = ()
    mode: str = "catalogue"
    max_file_size_mb: int = 100
    interval_seconds: int = 300
    enabled: bool = True

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Configuration":
        try:
            root = Path(data["root_path"])
            includes = data.get("included_paths", [])
            excludes = data.get("excluded_paths", [])
            if not isinstance(includes, list) or not isinstance(excludes, list):
                raise ValueError
            result = cls(
                root=root,
                included_paths=tuple(relative_path(item) for item in includes),
                excluded_paths=tuple(relative_path(item) for item in excludes),
                mode=data.get("mode", "catalogue"),
                max_file_size_mb=int(data.get("max_file_size_mb", 100)),
                interval_seconds=int(data.get("interval_seconds", 300)),
                enabled=data.get("enabled", True),
            )
            if result.mode not in ("catalogue", "mirror"):
                raise ValueError
            if not 1 <= result.max_file_size_mb <= MAX_FILE_SIZE_MB:
                raise ValueError
            if not 60 <= result.interval_seconds <= 86400:
                raise ValueError
            if not isinstance(result.enabled, bool) or not result.root.is_absolute():
                raise ValueError
            return result
        except (ValueError, TypeError, KeyError) as exc:
            raise ReplicaError("Invalid replica configuration") from exc

    def excluded(self, name: str) -> bool:
        normalized = name.casefold()
        return any(
            normalized == item.casefold() or normalized.startswith(item.casefold() + "/")
            for item in self.excluded_paths
        )


class Inventory:
    def __init__(self, config: Configuration):
        self.config = config
        self.errors: list[str] = []
        self.root = config.root.absolute()
        self.check_root()

    def check_root(self) -> None:
        try:
            # Reject links in the root and its existing parent path as well.
            for part in (self.root, *self.root.parents):
                if is_reparse(part.lstat()):
                    raise ReplicaError("Source root must not contain symlinks or junctions")
            if not self.root.is_dir():
                raise ReplicaError("Source root is not a directory")
            self.resolved_root = self.root.resolve(strict=True)
        except OSError as exc:
            raise ReplicaError("Cannot read source root; check share availability and permissions") from exc

    def safe_path(self, name: str) -> tuple[Path, os.stat_result]:
        normalized = relative_path(name)
        path = self.root
        for component in normalized.split("/"):
            path = path / component
            info = path.lstat()
            if is_reparse(info):
                raise ReplicaError("Symlinks and junctions are outside replica scope")
        if not path.resolve(strict=True).is_relative_to(self.resolved_root):
            raise ReplicaError("Path resolves outside the configured source root")
        return path, info

    def problem(self, message: str) -> None:
        # Bound diagnostics even for a very large inaccessible tree.
        if len(self.errors) < 100:
            self.errors.append(message)
        LOG.warning("%s", message)

    @contextmanager
    def open_source(self, name: str):
        path, before = self.safe_path(name)
        if not stat.S_ISREG(before.st_mode):
            raise ReplicaError("Only regular files can be uploaded")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            if signature(os.fstat(source.fileno())) != signature(before):
                raise ReplicaError("File changed while it was opened")
            self.safe_path(name)
            yield source, before
            _, after = self.safe_path(name)
            if signature(after) != signature(before) or signature(os.fstat(source.fileno())) != signature(before):
                raise ReplicaError("File changed during reading; retry on the next scan")

    def read_file(self, name: str, destination=None) -> tuple[str, os.stat_result]:
        limit = self.config.max_file_size_mb * 1024 * 1024
        with self.open_source(name) as (source, before):
            if before.st_size > limit:
                raise ReplicaError("File exceeds the configured upload size limit")
            digest = hashlib.sha256()
            count = 0
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                count += len(chunk)
                if count > limit:
                    raise ReplicaError("File grew beyond the configured upload size limit")
                digest.update(chunk)
                if destination is not None:
                    destination.write(chunk)
            if count != before.st_size:
                raise ReplicaError("File size changed during reading")
        return digest.hexdigest(), before

    def entry(self, name: str) -> dict[str, Any]:
        _, info = self.safe_path(name)
        is_directory = stat.S_ISDIR(info.st_mode)
        if not is_directory and not stat.S_ISREG(info.st_mode):
            raise ReplicaError("Only directories and regular files are supported")
        result = {
            "relative_path": name,
            "parent_path": str(PurePosixPath(name).parent) if "/" in name else "",
            "name": PurePosixPath(name).name,
            "is_directory": is_directory,
            "size_bytes": 0 if is_directory else info.st_size,
            "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            "checksum": "",
            "error": "",
        }
        if not is_directory and self.config.mode == "mirror":
            try:
                result["checksum"], verified = self.read_file(name)
                if signature(verified) != signature(info):
                    raise ReplicaError("File changed during inventory")
            except (ReplicaError, OSError) as exc:
                result["checksum"] = ""
                result["error"] = str(exc) if isinstance(exc, ReplicaError) else "Cannot read file"
                self.problem(f"{name}: {result['error']}")
        return result

    def entries(self) -> Iterator[dict[str, Any]]:
        emitted_directories: set[str] = set()
        pending_directories: deque[str] = deque()

        def visit(name: str, recurse: bool) -> Iterator[dict[str, Any]]:
            if self.config.excluded(name) or name.casefold() in emitted_directories:
                return
            try:
                item = self.entry(name)
                if item["is_directory"]:
                    emitted_directories.add(name.casefold())
                    if recurse:
                        pending_directories.append(name)
                yield item
            except (OSError, ReplicaError) as exc:
                detail = str(exc) if isinstance(exc, ReplicaError) else "Cannot enumerate path"
                self.problem(f"{name}: {detail}")

        if self.config.included_paths:
            selected: dict[str, str] = {}
            for name in self.config.included_paths:
                if self.config.excluded(name):
                    continue
                try:
                    _, info = self.safe_path(name)
                    if not stat.S_ISDIR(info.st_mode):
                        raise ReplicaError("Included paths must select project directories")
                except (OSError, ReplicaError) as exc:
                    detail = str(exc) if isinstance(exc, ReplicaError) else "Included folder is unavailable"
                    self.problem(f"{name}: {detail}")
                    continue
                selected.setdefault(name.casefold(), name)
            # An ancestor selection already covers its descendants. Removing
            # overlapping roots means each file is streamed only once without
            # retaining a set of every filename encountered on a large share.
            roots = [name for key, name in selected.items() if not any(
                "/".join(key.split("/")[:depth]) in selected
                for depth in range(1, len(key.split("/")))
            )]
            for name in roots:
                # Preserve navigation ancestors without reading their unselected
                # sibling subdirectories or files.
                components = name.split("/")
                for depth in range(1, len(components)):
                    yield from visit("/".join(components[:depth]), False)
                yield from visit(name, True)
            # Seed every selected project before expanding any of them. FIFO
            # expansion exposes their immediate children before deeper folders
            # in a large earlier project. Only directory paths are queued; one
            # scandir iterator and one metadata record are live at a time.
            while pending_directories:
                name = pending_directories.popleft()
                try:
                    path, _ = self.safe_path(name)
                    with os.scandir(path) as children:
                        sibling_keys: set[str] = set()
                        for child in children:
                            try:
                                child_name = relative_path(name + "/" + child.name)
                            except ReplicaError:
                                self.problem(f"{name}: Skipped an unsupported source filename")
                                continue
                            child_key = child_name.casefold()
                            if child_key in sibling_keys:
                                continue
                            sibling_keys.add(child_key)
                            yield from visit(child_name, True)
                except (OSError, ReplicaError) as exc:
                    detail = str(exc) if isinstance(exc, ReplicaError) else "Cannot enumerate path"
                    self.problem(f"{name}: {detail}")
        else:
            try:
                with os.scandir(self.root) as children:
                    for child in children:
                        try:
                            name = relative_path(child.name)
                        except ReplicaError:
                            self.problem("Skipped an unsupported source folder name")
                            continue
                        if self.config.excluded(name):
                            continue
                        if child.is_dir(follow_symlinks=False) or child.is_symlink():
                            yield from visit(name, False)
            except (OSError, ReplicaError):
                self.problem("Cannot completely enumerate source root")


def validate_api_url(value: str) -> str:
    parsed = urlsplit(value)
    loopback = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        not parsed.netloc or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or parsed.path.rstrip("/") != "/api/v1/file-replica/agent"
    ):
        raise ReplicaError("API URL must be HTTPS and end in /api/v1/file-replica/agent/ (HTTP allowed on loopback only)")
    return value.rstrip("/") + "/"


def valid_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReplicaError("API returned an invalid resource identifier") from exc


class API:
    def __init__(self, base_url: str, source_id: str, token: str, session=None):
        self.base_url = validate_api_url(base_url)
        self.source_id = valid_uuid(source_id)
        if not token or any(char.isspace() for char in token):
            raise ReplicaError("A valid RADAI_REPLICA_TOKEN is required")
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise ReplicaError("Install requirements-file-replica-agent.txt before connecting") from exc
            session = requests.Session()
        self.session = session
        self.session.headers.update({
            "Authorization": "Bearer " + token,
            "X-Replica-Source": self.source_id,
        })

    def request(self, method: str, endpoint: str, **kwargs) -> dict[str, Any]:
        # Never use an endpoint or upload URL supplied by a server response.
        if not re.fullmatch(r"config/|scans/|scans/[0-9a-f-]{36}/(?:entries|complete|heartbeat)/|entries/[0-9a-f-]{36}/content/", endpoint):
            raise ReplicaError("Invalid agent API endpoint")
        body = kwargs.get("data")
        attempts = kwargs.pop('attempts', 3)
        timeout = kwargs.pop('timeout', (15, 120))
        for attempt in range(attempts):
            if body is not None and hasattr(body, "seek"):
                body.seek(0)
            try:
                response = self.session.request(
                    method, self.base_url + endpoint, timeout=timeout,
                    allow_redirects=False, **kwargs,
                )
            except Exception as exc:
                # requests exceptions may contain request URLs; never log their text.
                if attempt == attempts - 1:
                    raise ReplicaError("Agent API connection failed after its retry limit") from exc
                time.sleep(2 ** attempt)
                continue
            try:
                if response.status_code in (429, 502, 503, 504):
                    if attempt < attempts - 1:
                        time.sleep(2 ** attempt)
                        continue
                if not 200 <= response.status_code < 300:
                    raise ReplicaError(f"Agent API returned HTTP {response.status_code}")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ReplicaError("Agent API returned invalid JSON") from exc
                if not isinstance(data, dict):
                    raise ReplicaError("Agent API returned an unexpected response")
                return data
            finally:
                response.close()
        raise ReplicaError("Agent API retry limit reached")

    def heartbeat_client(self):
        """A requests.Session is never shared across scanner and heartbeat threads."""
        import requests
        session = requests.Session()
        session.trust_env = self.session.trust_env
        session.verify = self.session.verify
        session.cert = self.session.cert
        session.proxies.update(self.session.proxies)
        token = self.session.headers['Authorization'].partition(' ')[2]
        return API(self.base_url, self.source_id, token, session=session)

    def upload(self, inventory: Inventory, entry: dict[str, Any], entry_id: str, scan_id: str) -> None:
        boundary = "radai-" + uuid.uuid4().hex
        with tempfile.TemporaryFile(mode="w+b") as payload:
            fields = {
                "scan_id": scan_id,
                "checksum": entry["checksum"],
                "modified_at": entry["modified_at"],
            }
            for name, value in fields.items():
                payload.write((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode())
            # A neutral filename keeps non-ASCII source names and header characters
            # out of multipart headers. The entry already contains the true name.
            payload.write((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"content.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode())
            checksum, info = inventory.read_file(entry["relative_path"], payload)
            modified = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
            if checksum != entry["checksum"] or info.st_size != entry["size_bytes"] or modified != entry["modified_at"]:
                raise ReplicaError("File changed after inventory; retry on the next scan")
            payload.write(f"\r\n--{boundary}--\r\n".encode())
            length = payload.tell()
            payload.seek(0)
            result = self.request("POST", f"entries/{valid_uuid(entry_id)}/content/", data=payload, headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(length),
            })
            if result.get("status") != "available":
                raise ReplicaError("Agent API did not confirm that uploaded content is available")


class ScanHeartbeat:
    def __init__(self, api: API, scan_id: str, interval=HEARTBEAT_SECONDS):
        self.api, self.scan_id, self.interval = api, scan_id, interval
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name='replica-scan-heartbeat', daemon=True)

    def run(self):
        client = None
        try:
            while not self.stopped.wait(self.interval):
                try:
                    if client is None:
                        client = self.api.heartbeat_client()
                    client.request('POST', f'scans/{self.scan_id}/heartbeat/', json={}, timeout=(5, 15), attempts=1)
                except Exception:
                    if not self.stopped.is_set():
                        LOG.warning('Scan heartbeat could not reach RADAI; inventory requests will retry independently')
        finally:
            if client is not None:
                client.session.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stopped.set()
        # A bounded join does not strand the scanner behind a network timeout.
        self.thread.join(timeout=1)


def synchronize(api: API, config: Configuration) -> bool:
    if not config.enabled:
        LOG.info("Source is disabled; scan skipped")
        return True
    scan = api.request("POST", "scans/", json={"run_id": str(uuid.uuid4())})
    scan_id = valid_uuid(scan.get("id"))
    error = ""
    count = 0
    last_progress_at = time.monotonic()
    last_progress_count = 0
    heartbeat = ScanHeartbeat(api, scan_id)
    heartbeat.__enter__()
    try:
        inventory = Inventory(config)

        def send_batch(batch: list[dict[str, Any]]) -> None:
            nonlocal last_progress_at, last_progress_count
            response = api.request("POST", f"scans/{scan_id}/entries/", json={"entries": batch})
            replies = response.get("entries")
            if not isinstance(replies, list) or len(replies) != len(batch):
                raise ReplicaError("Agent API did not acknowledge the complete inventory batch")
            pending = {entry["relative_path"]: entry for entry in batch}
            for reply in replies:
                if not isinstance(reply, dict) or reply.get("relative_path") not in pending:
                    raise ReplicaError("Agent API returned an unexpected inventory entry")
                entry = pending.pop(reply["relative_path"])
                if reply.get("upload_required"):
                    if config.mode != "mirror" or entry["is_directory"] or entry["error"] or not entry["checksum"]:
                        raise ReplicaError("Agent API requested an upload outside the configured scope")
                    try:
                        api.upload(inventory, entry, valid_uuid(reply.get("id")), scan_id)
                    except (OSError, ReplicaError) as exc:
                        detail = str(exc) if isinstance(exc, ReplicaError) else "Cannot read file for upload"
                        inventory.problem(f"{entry['relative_path']}: {detail}")
            now = time.monotonic()
            if count - last_progress_count >= 1000 or now - last_progress_at >= 30:
                LOG.info('Scan %s: %s entries acknowledged; continuing %s inventory', scan_id, count, config.mode)
                last_progress_at, last_progress_count = now, count

        batch = []
        for entry in inventory.entries():
            batch.append(entry)
            count += 1
            if len(batch) == BATCH_SIZE:
                send_batch(batch)
                batch = []
        if batch:
            send_batch(batch)
        if inventory.errors:
            error = "Inventory or upload incomplete: " + "; ".join(inventory.errors)
    except (OSError, ReplicaError) as exc:
        error = str(exc) if isinstance(exc, ReplicaError) else "Source read failed"
    finally:
        heartbeat.__exit__()
    completion = api.request("POST", f"scans/{scan_id}/complete/", json={"success": not bool(error), "error": error[:4000]})
    if completion.get("status") != "completed" and not error:
        error = "RADAI did not confirm successful completion; check the source scan status"
        LOG.warning("%s", error)
    LOG.info("Scan %s: %s entries, %s", scan_id, count, "incomplete" if error else "complete")
    return not bool(error)


def load_env_file(path: str | None) -> None:
    if not path:
        return
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise ReplicaError("Install python-dotenv to use --env-file") from exc
    env_path = Path(path)
    if not env_path.is_file():
        raise ReplicaError("The specified environment file does not exist")
    load_dotenv(env_path, override=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Dedicated connector .env file (existing environment takes precedence)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="Local inventory only; never connects to RADAI")
    action.add_argument("--check", action="store_true", help="Validate API configuration and source root without creating a scan")
    action.add_argument("--once", action="store_true", help="Run one synchronization (default)")
    action.add_argument("--watch", action="store_true", help="Repeat using the server-configured interval")
    parser.add_argument("--root", help="Local source root for --dry-run only")
    parser.add_argument("--include", action="append", default=[], help="Project folder relative to root; repeat as needed (--dry-run)")
    parser.add_argument("--exclude", action="append", default=[], help="Excluded subtree relative to root (--dry-run)")
    parser.add_argument("--mode", choices=("catalogue", "mirror"), default="catalogue", help="Dry-run mode; mirror also validates file reads and hashes")
    parser.add_argument("--max-file-size-mb", type=int, default=100, help="Dry-run upload size limit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.dry_run:
            if not args.root:
                raise ReplicaError("--dry-run requires --root")
            config = Configuration.from_api({
                "root_path": args.root, "included_paths": args.include,
                "excluded_paths": args.exclude, "mode": args.mode,
                "max_file_size_mb": args.max_file_size_mb,
            })
            inventory = Inventory(config)
            count = 0
            for entry in inventory.entries():
                print(json.dumps(entry, ensure_ascii=True))
                count += 1
            LOG.info("Dry run: %s entries, %s reported errors; no RADAI requests or source writes", count, len(inventory.errors))
            return 1 if inventory.errors else 0
        if args.root or args.include or args.exclude or args.mode != "catalogue" or args.max_file_size_mb != 100:
            raise ReplicaError("Local scope options are only accepted with --dry-run; configure connected scope in RADAI")
        load_env_file(args.env_file)
        api = API(
            os.environ.get("RADAI_REPLICA_API_URL", ""),
            os.environ.get("RADAI_REPLICA_SOURCE_ID", ""),
            os.environ.get("RADAI_REPLICA_TOKEN", ""),
        )
        while True:
            interval = 300
            try:
                config = Configuration.from_api(api.request("GET", "config/"))
                interval = config.interval_seconds
                if args.check:
                    Inventory(config)
                    LOG.info("API configuration and source root are accessible; mode=%s, selected folders=%s, enabled=%s", config.mode, len(config.included_paths), config.enabled)
                    return 0
                success = synchronize(api, config)
                if not args.watch:
                    return 0 if success else 1
            except ReplicaError as exc:
                if not args.watch:
                    raise
                LOG.error("%s", exc)
            # Interruptible by Ctrl+C; no background service is installed.
            time.sleep(interval)
    except (ReplicaError, OSError) as exc:
        LOG.error("%s", exc if isinstance(exc, ReplicaError) else "Local file operation failed")
        return 1
    except KeyboardInterrupt:
        LOG.info("Connector stopped")
        return 130


if __name__ == "__main__":
    sys.exit(main())
