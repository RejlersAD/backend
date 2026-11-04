"""Local catch-up pilot for synthetic RADAI-TEST emails; no cloud writes.

Default is preview only. --apply uses the existing local webhook. --watch
retries while this local process runs; cloud capture does not depend on it.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.sales.email_archive_source import ArchiveError, MAX_FILE_BYTES, payload_from_eml  # noqa: E402

PASS_BYTES = 128 * 1024 * 1024
ENDPOINT = 'http://127.0.0.1:8000/api/v1/sales/email-intake/'
ARCHIVE_ENDPOINT = ENDPOINT + 'archive-files/'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ArchiveError('redirect_refused')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_key(path):
    key = ''
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        name, separator, value = line.partition('=')
        if separator and name.strip() == 'SALES_EMAIL_INTAKE_WEBHOOK_KEY':
            key = value.strip()
            if key[:1] in {'"', "'"} and key[-1:] == key[:1]:
                key = key[1:-1]
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', key):
        raise ArchiveError('webhook_key_missing_or_unsupported')
    return key


def post_payload(payload, key, *, mailbox='', data=None):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    headers = {'X-RADAI-Webhook-Key': key}
    if data is None:
        body = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    else:
        boundary = uuid4().hex
        fields = [*payload.items(), ('mailbox', mailbox)]
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8')
                 for name, value in fields]
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="original_eml"; filename="source.eml"\r\n'
                      'Content-Type: message/rfc822\r\n\r\n'.encode(), data, f'\r\n--{boundary}--\r\n'.encode()])
        body = b''.join(parts)
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
    request = Request(ENDPOINT, data=body, method='POST', headers=headers)
    try:
        with opener.open(request, timeout=20) as response:
            data = response.read(65537)
            if response.status not in (200, 201) or len(data) > 65536:
                raise ArchiveError('invalid_intake_response')
            result = json.loads(data)
            if result.get('accepted') is not True or type(result.get('duplicate')) is not bool:
                raise ArchiveError('invalid_intake_response')
            UUID(result['intake_id'])
            return result['duplicate']
    except HTTPError as exc:
        raise ArchiveError(f'intake_http_{exc.code}') from None
    except (URLError, OSError):
        raise ArchiveError('local_backend_unavailable') from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ArchiveError('invalid_intake_response') from None


def post_archive(source_id, mailbox, key, data=None):
    headers = {'X-RADAI-Webhook-Key': key}
    if data is None:
        body = json.dumps({'source_message_id': source_id}).encode()
        headers['Content-Type'] = 'application/json'
    else:
        boundary = uuid4().hex
        parts = []
        for name, value in (('source_message_id', source_id), ('mailbox', mailbox)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="source.eml"\r\n'
                      'Content-Type: message/rfc822\r\n\r\n'.encode(), data,
                      f'\r\n--{boundary}--\r\n'.encode()])
        body = b''.join(parts)
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
    request = Request(ARCHIVE_ENDPOINT, data=body, method='POST', headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=90) as response:
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise ArchiveError('invalid_archive_response')
            result = json.loads(raw)
            if (result.get('state') not in {'awaiting_review', 'ready', 'complete'}
                    or type(result.get('file_count')) is not int or not 0 <= result['file_count'] <= 21):
                raise ArchiveError('invalid_archive_response')
            return result
    except HTTPError as exc:
        raise ArchiveError(f'archive_http_{exc.code}') from None
    except (URLError, OSError):
        raise ArchiveError('local_backend_unavailable') from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ArchiveError('invalid_archive_response') from None


def read_state(path):
    if not path.exists():
        return {}
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ArchiveError('checkpoint_too_large')
    try:
        state = json.loads(path.read_text('utf-8'))
        if not isinstance(state, dict) or any(not isinstance(value, dict) for value in state.values()):
            raise ValueError()
        return state
    except (ValueError, TypeError):
        raise ArchiveError('checkpoint_invalid') from None


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(state, output)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def local_lock(path):
    """An OS lock is released on process exit; stale lock files are harmless."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ArchiveError('catchup_already_running') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def watch(folder, mailbox, env_file, checkpoint, stop_file, log_file, *, sleep=time.sleep, archive_files=False):
    """Run only on this laptop; outages remain pending and no console is needed."""
    with local_lock(checkpoint.with_suffix('.lock')):
        stop_file.unlink(missing_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        log = logging.getLogger('radai_test_archive_watch')
        log.setLevel(logging.INFO)
        log.propagate = False
        log.addHandler(handler)
        try:
            log.info('Local test archive catch-up started.')
            while not stop_file.exists():
                try:
                    # Reload to recover from an unavailable source/configuration
                    # or a previous failed checkpoint write without restarting.
                    state = read_state(checkpoint)
                    key = read_key(env_file)
                    result = scan(folder, mailbox, apply=True, key=key,
                                  state=state, checkpoint=checkpoint, archive_files=archive_files)
                    log.info(json.dumps(result))
                except (ArchiveError, OSError):
                    log.info('Local archive configuration unavailable; retrying.')
                sleep(60)
            log.info('Local catch-up stopped by request.')
        finally:
            log.removeHandler(handler)
            handler.close()


def scan(folder, mailbox, *, apply=False, key='', state=None, checkpoint=None, send=post_payload,
         archive_files=False, handoff=post_archive):
    state = state if state is not None else {}
    counts = dict(found=0, ready=0, imported=0, duplicates=0, unchanged=0, skipped=0, failed=0)
    if archive_files:
        counts.update(linked_files=0, awaiting_review=0, attachment_warnings=0)
    errors = set()
    used = 0
    folder = folder.resolve(strict=True)
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() != '.eml':
            continue
        counts['found'] += 1
        try:
            if path.is_symlink() or not path.is_file() or path.resolve().parent != folder:
                raise ArchiveError('source_path_refused')
            before = path.stat()
            signature = [before.st_size, before.st_mtime_ns]
            previous = state.get(path.name, {})
            probe = None
            already_accepted = (apply and previous.get('signature') == signature
                                and previous.get('outcome') == 'accepted')
            if apply and previous.get('signature') == signature:
                if (previous.get('outcome') == 'skipped'
                        or (already_accepted and (not archive_files or previous.get('archive_state') == 'complete'))):
                    counts['unchanged'] += 1
                    continue
                if already_accepted and previous.get('source_id'):
                    probe = handoff(previous['source_id'], mailbox, key)
                    if probe['state'] == 'awaiting_review':
                        counts['unchanged'] += 1
                        counts['awaiting_review'] += 1
                        continue
            if before.st_size > MAX_FILE_BYTES:
                raise ArchiveError('email_too_large')
            if used + before.st_size > PASS_BYTES:
                errors.add('remaining_files_deferred_to_next_pass')
                break
            with path.open('rb') as source:
                data = source.read(MAX_FILE_BYTES + 1)
            used += len(data)
            after = path.stat()
            if signature != [after.st_size, after.st_mtime_ns] or len(data) != before.st_size:
                raise ArchiveError('source_still_syncing')
            payload = payload_from_eml(data, mailbox)
            if payload is None:
                counts['skipped'] += 1
                outcome = 'skipped'
            else:
                counts['ready'] += 1
                outcome = 'accepted'
                if already_accepted:
                    counts['unchanged'] += 1
                elif apply:
                    duplicate = (send(payload, key, mailbox=mailbox, data=data) if send is post_payload
                                 else send(payload, key))
                    counts['duplicates' if duplicate else 'imported'] += 1
            if apply:
                state[path.name] = {'signature': signature, 'outcome': outcome}
                if payload:
                    state[path.name]['source_id'] = payload['source_message_id']
                if checkpoint:
                    write_state(checkpoint, state)
                if payload and archive_files:
                    # Save the intake acknowledgement before trying files.
                    outcome = probe or handoff(payload['source_message_id'], mailbox, key)
                    if outcome['state'] == 'ready':
                        outcome = handoff(payload['source_message_id'], mailbox, key, data=data)
                    if outcome['state'] == 'complete':
                        state[path.name]['archive_state'] = 'complete'
                        counts['linked_files'] += outcome['file_count']
                        counts['attachment_warnings'] += len(outcome.get('warnings', []))
                    else:
                        counts['awaiting_review'] += 1
                    if checkpoint:
                        write_state(checkpoint, state)
        except ArchiveError as exc:
            counts['failed'] += 1
            errors.add(str(exc))
            if str(exc).startswith(('intake_', 'archive_', 'local_backend_', 'redirect_')):
                break  # Preserve pending sources; do not hammer an unavailable backend.
        except (OSError, ValueError, TypeError, LookupError):
            counts['failed'] += 1
            errors.add('source_or_checkpoint_unavailable')
    return {**counts, 'errors': sorted(errors)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mailbox', required=True)
    parser.add_argument('--apply', action='store_true', help='Import through the local webhook.')
    parser.add_argument('--watch', action='store_true', help='Repeat locally every 60 seconds.')
    parser.add_argument('--stop-watch', action='store_true', help='Stop this mailbox watcher within 60 seconds.')
    parser.add_argument('--archive-files', action='store_true', help='Link files after reviewed test conversion (backend opt-in required).')
    parser.add_argument('--folder', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', args.mailbox):
        parser.error('Provide the company mailbox receiving the test emails.')
    if args.watch and not args.apply:
        parser.error('--watch requires --apply; preview is a single read-only pass.')
    if args.archive_files and not args.apply:
        parser.error('--archive-files requires --apply.')
    sync_root = os.environ.get('OneDriveCommercial')
    if args.folder is None and not sync_root:
        parser.error('Company OneDrive is not configured; provide --folder.')
    folder = args.folder or Path(sync_root) / 'RADAI-Email-Test' / '01 Correspondence' / 'Incoming Emails'
    workspace = Path(__file__).resolve().parents[2]
    state_root = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'RADAI' / 'test-email-archive'
    scope = digest((str(folder.resolve()) + '\0' + args.mailbox.casefold()).encode())[:24]
    checkpoint = state_root / (scope + '.json')
    stop_file = state_root / (scope + '.stop')
    try:
        if args.stop_watch:
            state_root.mkdir(parents=True, exist_ok=True)
            stop_file.touch()
            print('Local catch-up stop requested. Cloud capture is unchanged.')
            return 0
        if args.watch:
            watch(folder, args.mailbox, workspace / '.env.local', checkpoint,
                  stop_file, state_root / (scope + '.log'), archive_files=args.archive_files)
            return 0
        if args.apply:
            with local_lock(checkpoint.with_suffix('.lock')):
                state = read_state(checkpoint)
                key = read_key(workspace / '.env.local')
                result = scan(folder, args.mailbox, apply=True, key=key,
                              state=state, checkpoint=checkpoint, archive_files=args.archive_files)
        else:
            result = scan(folder, args.mailbox)
        print(json.dumps({'mode': 'import' if args.apply else 'preview', **result}), flush=True)
        return 1 if result['failed'] else 0
    except KeyboardInterrupt:
        return 0
    except ArchiveError as exc:
        if str(exc) == 'catchup_already_running':
            print('Local catch-up is already running. No second copy was started.')
            return 0
        print('Local archive configuration unavailable. No source files were changed.', file=sys.stderr)
        return 1
    except OSError:
        print('Local archive configuration unavailable. No source files were changed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
