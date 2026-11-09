"""Verified report manifest based on stored follower and About records.

A snapshot is only an audit of local records at export time, not a guarantee
of third-party data completeness or a cryptographic signature of the workbook.
"""
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
from filtering import exportable_rows
from readiness import inspect_job, inspect_group


def _utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def manifest_for_job(db, job_id):
    from core import followers_as_dicts
    raw = followers_as_dicts(db, job_id)
    valid = exportable_rows(raw)
    check = inspect_job(db, job_id)
    countries=Counter(r['account_country'] for r in valid)
    result = {
        'scope': 'job', 'scope_id': int(job_id), 'checked_at': _utc(),
        'status': 'نهائي معتمد' if check['approved'] else 'أولي غير معتمد',
        'approved': bool(check['approved']), 'reason': check['reason'],
        'total_followers': len(raw), 'exported': len(valid),
        'excluded_without_country': len(raw)-len(valid),
        'about_done': check['about_done'], 'about_pending': check['about_pending'],
        'about_exhausted': check['about_exhausted'],
        'about_missing_username': check['about_missing_username'],
        'fetch_complete': bool(check['fetch_complete']),
        'countries': dict(sorted(countries.items())),
    }
    result['audit_id'] = _digest(result)
    return result


def manifest_for_group(db, group_id):
    from multi_accounts import members
    group_check=inspect_group(db, group_id)
    checks=[manifest_for_job(db, m['job_id']) for m in members(db,group_id)]
    result = {
        'scope':'group','scope_id':int(group_id),'checked_at':_utc(),
        'status': 'نهائي معتمد' if group_check['approved'] else 'أولي غير معتمد',
        'approved': bool(group_check['approved']), 'reason':group_check['reason'],
        'member_count':len(checks),'approved_members':sum(m['approved'] for m in checks),
        'total_followers':sum(m['total_followers'] for m in checks),
        'exported':sum(m['exported'] for m in checks),
        'excluded_without_country':sum(m['excluded_without_country'] for m in checks),
        'about_done':sum(m['about_done'] for m in checks),
        'about_pending':sum(m['about_pending'] for m in checks),
        'about_exhausted':sum(m['about_exhausted'] for m in checks),
        'about_missing_username':sum(m['about_missing_username'] for m in checks),
        'members':checks,
    }
    result['audit_id']=_digest(result)
    return result


def _digest(data):
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def audit_lines(manifest):
    return [
        ('حالة التقرير',manifest['status']),
        ('وقت التحقق',manifest['checked_at']),
        ('المتابعون المسجلون',manifest['total_followers']),
        ('الحسابات المؤهلة للتقرير',manifest['exported']),
        ('الحسابات المستبعدة لغياب دولة About',manifest['excluded_without_country']),
        ('فحوص About المكتملة',manifest['about_done']),
        ('فحوص About المعلقة',manifest['about_pending']),
        ('فحوص About المستنفدة',manifest['about_exhausted']),
        ('فحوص بلا يوزر',manifest['about_missing_username']),
        ('سبب الحالة',manifest['reason']),
        ('مرجع التحقق الداخلي',manifest['audit_id']),
    ]
