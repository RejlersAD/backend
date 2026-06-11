"""
Mirror-mode query services — produce the same JSON shapes as `services.py`
but reading from the Postgres `TimesheetEvent` table (populated by the
office-side sync agent) instead of the on-prem SQL Server.

Used when `TIMESHEET_DATA_SOURCE=mirror` (Railway/production). Same
employee-enrichment helper from `services.py` is reused so the frontend
never sees a different payload shape.
"""
from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from typing import Optional

from django.db.models import Max, Min
from django.utils import timezone

from . import config as ts_config
from .models import TimesheetEvent, BiometricUserMaster
from .services import (
    _enrich_with_rad_users, _backfill_email_from_matrix_name,
    _hours_between, _is_late, _empty_summary,
    _working_days, _parse_date,
    # Soft-coded helpers reused from the SQL Server backend so both data
    # sources behave identically when matching a RAD AI user → biometric row.
    _norm_key, _name_tokens,
    _USER_NAME_RESOLVE, _USER_NAME_RESOLVE_MIN_TOKS,
    _USER_NAME_RESOLVE_MAX_HITS, _USER_NAME_RESOLVE_TTL_SEC,
)


_VARIANT = 'mirror'


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last_day)


def _to_naive(t):
    """Strip tz to keep frontend formatting consistent with sqlserver path."""
    if t is None:
        return None
    if hasattr(t, 'tzinfo') and t.tzinfo is not None:
        return t.astimezone(timezone.get_current_timezone()).replace(tzinfo=None)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded BiometricUserMaster enrichment (mirror backend)
# ─────────────────────────────────────────────────────────────────────────────
# Map model field → row payload key. Same alias scheme as the SQL Server
# `_enrich_with_user_details` so the frontend reads the same keys in either
# data source. Add a row here to expose a new column on production.
_USER_MASTER_ROW_FIELDS = (
    ('card1',          'card1'),
    ('card2',          'card2'),
    ('office_email',   'office_email'),
    ('personal_email', 'personal_email'),
    ('full_name',      'matrix_full_name'),
    ('designation',    'designation'),
    ('department',     'department_master'),
)


def _enrich_from_user_master_mirror(rows: list[dict]) -> list[dict]:
    """Merge `BiometricUserMaster` columns (Card1, OfficeEmail, FullName …)
    into each attendance row by `employee_code`. Safe-by-default: if the
    table is empty (agent hasn't synced yet) the rows are returned unchanged
    with the new keys absent — the frontend already renders that as `—`."""
    if not rows:
        return rows
    codes = [str(r.get('employee_code') or '').strip()
             for r in rows if r.get('employee_code')]
    if not codes:
        return rows
    try:
        master = {
            m.employee_code: m
            for m in BiometricUserMaster.objects.filter(employee_code__in=codes)
        }
    except Exception:
        return rows
    if not master:
        return rows
    for r in rows:
        m = master.get(str(r.get('employee_code') or '').strip())
        if not m:
            continue
        for src, alias in _USER_MASTER_ROW_FIELDS:
            val = getattr(m, src, '') or ''
            if alias not in r or r.get(alias) in (None, ''):
                r[alias] = val
        # Backfill `employee_name` (used by `_backfill_email_from_matrix_name`)
        # if the attendance event itself shipped without a name.
        if m.full_name and not (r.get('employee_name') or r.get('name')):
            r['employee_name'] = m.full_name
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded biometric employee_code resolver (mirror backend)
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_biometric_codes_mirror(*, profile=None,
                                    email: Optional[str] = None,
                                    employee_code: Optional[str] = None) -> set[str]:
    """Resolve a RAD AI user → set of `TimesheetEvent.employee_code` values
    via fuzzy ``employee_name__icontains`` matching. Same multi-strategy
    approach as ``services._resolve_biometric_user_ids`` but operating on the
    Postgres mirror table — so Railway/production users whose biometric
    `employee_code` (system-generated badge number) bears no relation to
    their RAD AI ``UserProfile.employee_id`` still resolve.
    """
    if not _USER_NAME_RESOLVE:
        return set()

    first = (profile.user.first_name if profile and profile.user else '') or ''
    last  = (profile.user.last_name  if profile and profile.user else '') or ''
    # Try strategies in order of confidence — first one yielding 1..N hits wins.
    # NOTE: 'email_exact' and 'last_name_only' are mirror-only strategies that
    # leverage columns the SQL Server biometric view does not expose. Both are
    # soft-bounded by MAX_HITS so an over-broad match never resolves.
    strategies: list[tuple[str, tuple]] = [
        ('email_stem',          (email,)),
        ('email_and_code_stem', (email, employee_code)),
        ('profile_full_name',   (first, last)),
        ('profile_and_email',   (first, last, email)),
    ]

    # Versioned cache key — bumping _CACHE_VER below invalidates ALL stale
    # entries from older deploys (e.g. a previous deploy that cached an
    # empty result before the resolver was active).
    _CACHE_VER = 'v2'
    cache_key = f'ts:bio_uid_mirror:{_CACHE_VER}:{(_norm_key(email) or _norm_key(employee_code))[:128]}'
    cache = None
    try:
        from django.core.cache import cache as _c
        cache = _c
        cached = cache.get(cache_key)
        if cached is not None:
            return set(cached)
    except Exception:
        cache = None

    chosen_hits: list[dict] = []
    chosen_strategy = None
    chosen_tokens: list[str] = []

    # Strategy 0 — direct email match against the mirror's `employee_email`
    # column. The biometric sync agent populates this when the source system
    # exposes an email; when it does, this is the most reliable signal and
    # zero-token-dependent (works even if the user's biometric `employee_name`
    # is mis-spelled, abbreviated, or stored in a non-Latin script).
    if email:
        email_norm = _norm_key(email)
        if email_norm:
            email_hits = list(
                TimesheetEvent.objects
                    .filter(employee_email__iexact=email_norm)
                    .values('employee_code', 'employee_name')
                    .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
            )
            if 0 < len(email_hits) <= _USER_NAME_RESOLVE_MAX_HITS:
                chosen_hits = email_hits
                chosen_strategy = 'email_exact'
                chosen_tokens = [email_norm]

    for label, sources in strategies:
        if chosen_hits:
            break
        tokens = _name_tokens(*sources)
        if len(tokens) < _USER_NAME_RESOLVE_MIN_TOKS:
            continue
        qs = TimesheetEvent.objects.all()
        for t in tokens:
            qs = qs.filter(employee_name__icontains=t)
        hits = list(
            qs.values('employee_code', 'employee_name')
              .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
        )
        if 0 < len(hits) <= _USER_NAME_RESOLVE_MAX_HITS:
            chosen_hits = hits
            chosen_strategy = label
            chosen_tokens = tokens
            break

    # Last-resort — most-distinctive single token (usually the surname).
    # Only runs when every multi-token strategy returned 0 hits. Bounded by
    # MAX_HITS so a too-common token (e.g. 'mohammed' across 50 rows) is
    # rejected rather than guessed.
    if not chosen_hits:
        all_tokens = _name_tokens(email, employee_code, first, last)
        if all_tokens:
            # Longest token first — most discriminating.
            for t in sorted(set(all_tokens), key=len, reverse=True):
                hits = list(
                    TimesheetEvent.objects
                        .filter(employee_name__icontains=t)
                        .values('employee_code', 'employee_name')
                        .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
                )
                if 0 < len(hits) <= _USER_NAME_RESOLVE_MAX_HITS:
                    chosen_hits = hits
                    chosen_strategy = f'single_token({t})'
                    chosen_tokens = [t]
                    break

    if not chosen_hits:
        if cache is not None:
            try: cache.set(cache_key, [], _USER_NAME_RESOLVE_TTL_SEC)
            except Exception: pass
        return set()

    resolved = {str(h.get('employee_code') or '').strip() for h in chosen_hits if h.get('employee_code')}
    resolved.discard('')
    import logging
    logging.getLogger(__name__).info(
        'Timesheet mirror name-resolver[%s]: tokens=%s → codes=%s (%s)',
        chosen_strategy, chosen_tokens, sorted(resolved),
        [(h.get('employee_code'), h.get('employee_name')) for h in chosen_hits],
    )
    if cache is not None:
        try: cache.set(cache_key, list(resolved), _USER_NAME_RESOLVE_TTL_SEC)
        except Exception: pass
    return resolved


def _resolve_user_aliases_mirror(employee_code: Optional[str],
                                 email: Optional[str]) -> tuple[set[str], set[str]]:
    """Return ({normalised emails}, {biometric employee_codes}) for a RAD AI
    user. Combines profile aliases with fuzzy-resolved biometric codes."""
    emails: set[str] = set()
    codes: set[str] = set()
    if email:
        emails.add(_norm_key(email))
    if employee_code:
        codes.add(str(employee_code).strip())

    resolved_profile = None
    try:
        from apps.rbac.models import UserProfile
        from django.db.models import Q
        cond = None
        if email:
            cond = Q(user__email__iexact=str(email).strip())
        if employee_code:
            c2 = Q(employee_id__iexact=str(employee_code).strip())
            cond = c2 if cond is None else (cond | c2)
        if cond is not None:
            for p in UserProfile.objects.select_related('user').filter(cond, is_deleted=False):
                resolved_profile = resolved_profile or p
                if p.user and p.user.email:
                    emails.add(_norm_key(p.user.email))
                if p.employee_id:
                    codes.add(str(p.employee_id).strip())
    except Exception:
        # Never let RAD AI lookup failure break biometric reporting
        pass

    try:
        codes |= _resolve_biometric_codes_mirror(
            profile=resolved_profile, email=email, employee_code=employee_code,
        )
    except Exception:
        pass

    emails.discard('')
    codes.discard('')
    return emails, codes


# ─────────────────────────────────────────────────────────────────────────────
# Live status — latest punch per user today
# ─────────────────────────────────────────────────────────────────────────────
def live_status() -> dict:
    today = timezone.localdate()
    qs = TimesheetEvent.objects.filter(event_time__date=today)

    # Latest punch per employee_code
    latest_by_emp: dict[str, TimesheetEvent] = {}
    for ev in qs.order_by('employee_code', 'event_time'):
        latest_by_emp[ev.employee_code] = ev  # last write wins → latest

    rows = []
    for ev in latest_by_emp.values():
        rows.append({
            'employee_code': ev.employee_code,
            'email': ev.employee_email or None,
            'name': ev.employee_name,
            'employee_name': ev.employee_name,
            'department': ev.department,
            'punch_time': _to_naive(ev.event_time),
            'punch_type': ev.event_type,
        })

    rows = _enrich_from_user_master_mirror(rows)
    rows = _enrich_with_rad_users(rows)
    rows = _backfill_email_from_matrix_name(rows)

    summary = _empty_summary()
    for r in rows:
        if str(r.get('punch_type', '')).upper() == TimesheetEvent.EVENT_IN:
            summary['currently_in'] += 1
        else:
            summary['currently_out'] += 1
        if _is_late(r):
            summary['late_today'] += 1
    summary['total_seen_today'] = len(rows)
    summary['matched_to_radai'] = sum(1 for r in rows if r.get('radai_user_id'))

    rows.sort(key=lambda r: r.get('punch_time') or dt.datetime.min, reverse=True)
    return {
        'rows': rows,
        'summary': summary,
        'variant': _VARIANT,
        'as_of': dt.datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Daily report
# ─────────────────────────────────────────────────────────────────────────────
def daily_report(date: Optional[str] = None) -> dict:
    day = _parse_date(date)
    qs = (
        TimesheetEvent.objects
        .filter(event_time__date=day)
        .values('employee_code')
        .annotate(first_in=Min('event_time'), last_out=Max('event_time'))
    )

    # Need names/departments — fetch once per employee
    meta = {
        e['employee_code']: e
        for e in TimesheetEvent.objects
        .filter(event_time__date=day)
        .values('employee_code', 'employee_name', 'employee_email', 'department')
        .distinct()
    }

    rows = []
    for r in qs:
        m = meta.get(r['employee_code'], {})
        first_in = _to_naive(r['first_in'])
        last_out = _to_naive(r['last_out'])
        hours = _hours_between(first_in, last_out) or 0
        rows.append({
            'employee_code': r['employee_code'],
            'email': m.get('employee_email') or None,
            'name': m.get('employee_name', ''),
            'employee_name': m.get('employee_name', ''),
            'department': m.get('department', ''),
            'first_in': first_in,
            'last_out': last_out,
            'hours_worked': hours,
            'is_late': _is_late({'punch_time': first_in}),
            'is_full_day': hours >= ts_config.RULES['full_day_hours'],
        })

    rows = _enrich_from_user_master_mirror(rows)
    rows = _enrich_with_rad_users(rows)
    rows = _backfill_email_from_matrix_name(rows)
    rows.sort(key=lambda r: r.get('first_in') or dt.datetime.min)
    return {'date': day.isoformat(), 'rows': rows, 'variant': _VARIANT}


# ─────────────────────────────────────────────────────────────────────────────
# Monthly report
# ─────────────────────────────────────────────────────────────────────────────
def monthly_report(year: Optional[int] = None, month: Optional[int] = None) -> dict:
    today = dt.date.today()
    y = int(year or today.year)
    m = int(month or today.month)
    start, end = _month_range(y, m)
    end_exclusive = end + dt.timedelta(days=1)

    qs = (
        TimesheetEvent.objects
        .filter(event_time__gte=start, event_time__lt=end_exclusive)
        .values('employee_code', 'event_time__date')
        .annotate(first_in=Min('event_time'), last_out=Max('event_time'))
    )

    meta = {
        e['employee_code']: e
        for e in TimesheetEvent.objects
        .filter(event_time__gte=start, event_time__lt=end_exclusive)
        .values('employee_code', 'employee_name', 'employee_email', 'department')
        .distinct()
    }

    by_emp: dict[str, dict] = {}
    for r in qs:
        key = r['employee_code']
        m_ = meta.get(key, {})
        slot = by_emp.setdefault(key, {
            'employee_code': key,
            'email': m_.get('employee_email') or None,
            'name': m_.get('employee_name', ''),
            'employee_name': m_.get('employee_name', ''),
            'department': m_.get('department', ''),
            'days_present': 0,
            'full_days': 0,
            'half_days': 0,
            'late_arrivals': 0,
            'total_hours': 0.0,
            'days_detail': [],
        })
        first_in = _to_naive(r['first_in'])
        last_out = _to_naive(r['last_out'])
        hours = _hours_between(first_in, last_out) or 0
        slot['days_present'] += 1
        slot['total_hours'] += hours
        if hours >= ts_config.RULES['full_day_hours']:
            slot['full_days'] += 1
        else:
            slot['half_days'] += 1
        if _is_late({'punch_time': first_in}):
            slot['late_arrivals'] += 1
        slot['days_detail'].append({
            'date': str(r['event_time__date']),
            'first_in': str(first_in) if first_in else None,
            'last_out': str(last_out) if last_out else None,
            'hours': round(hours, 2),
        })

    rows = list(by_emp.values())
    for slot in rows:
        slot['total_hours'] = round(slot['total_hours'], 2)
        slot['avg_hours_per_day'] = (
            round(slot['total_hours'] / slot['days_present'], 2) if slot['days_present'] else 0
        )
    rows = _enrich_from_user_master_mirror(rows)
    rows = _enrich_with_rad_users(rows)
    rows = _backfill_email_from_matrix_name(rows)
    rows.sort(key=lambda x: (x.get('radai_full_name') or x.get('name') or ''))
    return {
        'year': y,
        'month': m,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'working_days_in_month': _working_days(start, end),
        'rows': rows,
        'variant': _VARIANT,
    }


def lookup_by_code(code: str) -> Optional[dict]:
    """Reverse lookup against the Postgres mirror — biometric ``employee_code``
    → ``{employee_code, employee_name, employee_email}``. Same contract as
    ``services.lookup_by_code`` so the dispatcher in views can stay backend-
    agnostic."""
    code = (code or '').strip()
    if not code:
        return None
    row = (
        TimesheetEvent.objects
            .filter(employee_code=code)
            .values('employee_code', 'employee_name', 'employee_email')
            .first()
    )
    return row or None


# ─────────────────────────────────────────────────────────────────────────────
# Per-user drill-down
# ─────────────────────────────────────────────────────────────────────────────
def user_history(employee_code: Optional[str] = None,
                 email: Optional[str] = None,
                 from_date: Optional[str] = None,
                 to_date: Optional[str] = None,
                 include_punches: bool = False) -> dict:
    today = dt.date.today()
    start = _parse_date(from_date, today - dt.timedelta(days=30))
    end = _parse_date(to_date, today)
    end_exclusive = end + dt.timedelta(days=1)

    if not (employee_code or email):
        return {'rows': [], 'error': 'employee_code or email required'}

    from django.db.models import Q
    import logging
    log = logging.getLogger(__name__)
    qs = TimesheetEvent.objects.filter(event_time__gte=start, event_time__lt=end_exclusive)
    # OR-match: either identifier may resolve the record. Aliases include
    # alternate emails from the RAD AI UserProfile AND biometric employee_codes
    # discovered by fuzzy [employee_name] match — same multi-strategy logic
    # used by the SQL Server backend in services._resolve_user_aliases.
    alias_emails, alias_codes = _resolve_user_aliases_mirror(employee_code, email)
    log.info(
        'timesheet.user_history.mirror inputs code=%r email=%r → aliases emails=%s codes=%s',
        employee_code, email, sorted(alias_emails), sorted(alias_codes),
    )
    cond = Q()
    has_filter = False
    if alias_codes:
        cond |= Q(employee_code__in=list(alias_codes))
        has_filter = True
    for e in alias_emails:
        cond |= Q(employee_email__iexact=e)
        has_filter = True
    if not has_filter:
        return {'rows': [], 'error': 'employee_code or email required'}
    qs = qs.filter(cond)
    log.info('timesheet.user_history.mirror matched %d events', qs.count())

    per_day = defaultdict(lambda: {'first_in': None, 'last_out': None, 'punches': 0})
    raw_punches: list[dict] = []  # optional, per-event detail
    employee_meta = {'employee_code': '', 'employee_name': '', 'employee_email': '', 'department': ''}

    for ev in qs.order_by('event_time'):
        d = ev.event_time.date().isoformat()
        slot = per_day[d]
        ts = _to_naive(ev.event_time)
        if slot['first_in'] is None or ts < slot['first_in']:
            slot['first_in'] = ts
        if slot['last_out'] is None or ts > slot['last_out']:
            slot['last_out'] = ts
        slot['punches'] += 1
        # First seen wins for the employee header metadata
        if not employee_meta['employee_code']:
            employee_meta = {
                'employee_code':  ev.employee_code,
                'employee_name':  ev.employee_name,
                'employee_email': ev.employee_email,
                'department':     ev.department,
            }
        if include_punches:
            raw_punches.append({
                'event_time': ts.isoformat() if ts else None,
                'event_type': ev.event_type,
                'date':       d,
            })

    # Per-day rows (same shape as before)
    rows = []
    for d, slot in sorted(per_day.items()):
        hours = _hours_between(slot['first_in'], slot['last_out']) or 0
        rows.append({
            'date':         d,
            'first_in':     str(slot['first_in']) if slot['first_in'] else None,
            'last_out':     str(slot['last_out']) if slot['last_out'] else None,
            'hours_worked': round(hours, 2),
            'punch_count':  slot['punches'],
        })

    # ── Consolidated summary across the whole range
    full_day_hours = float(ts_config.RULES.get('full_day_hours', 8.0))
    total_hours    = sum(r['hours_worked'] for r in rows)
    total_punches  = sum(r['punch_count'] or 0 for r in rows)
    days_present   = len(rows)
    days_full      = sum(1 for r in rows if (r['hours_worked'] or 0) >= full_day_hours)
    days_partial   = days_present - days_full

    # Average punch-in / punch-out time (HH:MM)
    def _avg_time(values):
        valid = [v for v in values if v]
        if not valid:
            return None
        secs = [v.hour * 3600 + v.minute * 60 + v.second for v in valid]
        avg = sum(secs) // len(secs)
        return f'{avg // 3600:02d}:{(avg % 3600) // 60:02d}'

    first_ins = []
    last_outs = []
    for d, slot in per_day.items():
        if slot['first_in']:
            first_ins.append(slot['first_in'])
        if slot['last_out'] and slot['last_out'] != slot['first_in']:
            last_outs.append(slot['last_out'])

    summary = {
        'total_hours':         round(total_hours, 2),
        'total_punches':       total_punches,
        'days_present':        days_present,
        'days_full':           days_full,
        'days_partial':        days_partial,
        'avg_hours_per_day':   round(total_hours / days_present, 2) if days_present else 0,
        'avg_first_in':        _avg_time(first_ins),
        'avg_last_out':        _avg_time(last_outs),
        'range_days':          (end - start).days + 1,
    }

    # ── Monthly breakdown (one entry per YYYY-MM in range)
    monthly_buckets: dict[str, dict] = defaultdict(lambda: {
        'hours': 0.0, 'days': 0, 'punches': 0,
    })
    for r in rows:
        ym = r['date'][:7]  # 'YYYY-MM'
        b = monthly_buckets[ym]
        b['hours']   += r['hours_worked'] or 0
        b['days']    += 1
        b['punches'] += r['punch_count'] or 0
    monthly_breakdown = [
        {
            'month':        ym,
            'hours':        round(b['hours'], 2),
            'days_present': b['days'],
            'punches':      b['punches'],
            'avg_per_day':  round(b['hours'] / b['days'], 2) if b['days'] else 0,
        }
        for ym, b in sorted(monthly_buckets.items())
    ]

    return {
        'employee':          employee_meta,
        'from':              start.isoformat(),
        'to':                end.isoformat(),
        'rows':              rows,
        'summary':           summary,
        'monthly_breakdown': monthly_breakdown,
        'punches':           raw_punches if include_punches else None,
        'variant':           _VARIANT,
    }
