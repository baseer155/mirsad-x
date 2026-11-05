"""Reporting-only transformations; no fabricated account attributes or network calls."""
from __future__ import annotations
from datetime import datetime, timezone
import re

UNAVAILABLE = 'غير متاح من المصدر'

def _text(value):
    if value is None: return ''
    text = str(value).strip()
    return '' if text.lower() in ('none', 'null', 'nan', '', 'unknown') else text

def account_links(row):
    uid = _text(row.get('uid'))
    username = _text(row.get('username')).lstrip('@')
    if not re.fullmatch(r'\d{1,25}', uid): uid = ''
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', username): username = ''
    return (
        f'https://x.com/i/user/{uid}' if uid else '',
        f'https://x.com/{username}' if username else '',
        f'https://x.com/{username}/about' if username else '',
    )

def account_status(row):
    """ONLY explicit evidence. Unknown never becomes active or suspended."""
    val = _text(row.get('suspension_status') or row.get('account_status')).casefold()
    if val in {'موقوف', 'موقوف حسب المصدر', 'suspended', 'account suspended'}: return 'موقوف'
    if val in {'فعال', 'غير موقوف حسب المصدر', 'active', 'not suspended', 'not_suspended'}: return 'فعال'
    if val in {'محجوز في السعودية', 'withheld in saudi arabia', 'withheld in sa', 'withheld_sa'}: return 'محجوز في السعودية'
    raw = row.get('withheld_in_countries')
    if isinstance(raw, (list,tuple,set)) and 'SA' in {str(x).upper() for x in raw}:
        return 'محجوز في السعودية'
    # Lack of a public profile alone is not proof of suspension.
    return 'غير متاح من المصدر'

def connected_via(row):
    val = _text(row.get('connected_via'))
    return val if val and val not in {'لم يُفحص بعد','غير متاح من المصدر'} else UNAVAILABLE

def safe_report_name(display_name, username, extension):
    def clean(value):
        t=re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_',_text(value))
        return re.sub(r'\s+',' ',t).strip(' ._')[:65]
    user=clean(username).lstrip('@') or 'unknown'
    label=clean(display_name) or user
    ext=extension.lstrip('.')
    if ext not in {'xlsx','docx','pptx'}:raise ValueError('unsupported extension')
    return f'{label}_@{user}.{ext}'

def elapsed_metrics(job, events):
    """Expose actual counters separately from wall-clock elapsed, never label an API timer as wall time."""
    api=float(job.get('elapsed_seconds') or 0)
    started=_parse_time(job.get('started_at'))
    updated=_parse_time(job.get('updated_at'))
    wall=max(0,(updated-started).total_seconds()) if updated and started else None
    # elapsed_seconds is X follower API time in existing DB; does not include enrichment requests.
    about_events=sum(1 for e in events if 'About' in _text(e.get('kind')) or 'حول' in _text(e.get('kind')))
    pages=int(job.get('pages') or 0)
    unique=int(job.get('unique_count') or job.get('unique') or 0)
    return {'api_seconds':api,'wall_seconds':wall,'about_events':about_events,'pages':pages,
            'seconds_per_page':api/pages if pages else None,
            'unique_per_minute':unique*60/api if api>0 and unique else None}

def _parse_time(value):
    try:
        if not value:return None
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError,TypeError): return None

def fmt_duration(seconds):
    if seconds is None:return 'غير مسجل'
    seconds=max(0,int(round(float(seconds))))
    return f'{seconds//3600:02d}:{(seconds%3600)//60:02d}:{seconds%60:02d}'
