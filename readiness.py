"""Deterministic, conservative final-report eligibility checks.

No side effects on status: this module NEVER declares success just because the
UI timer stopped or a requested numeric target was reached.
"""
from auto_enrich import stats


def inspect_job(db, job_id):
    job = db.execute('SELECT id,target,status,pages,scanned FROM jobs WHERE id=?', (job_id,)).fetchone()
    if job is None:
        raise ValueError('المهمة غير موجودة')
    a = stats(db, job_id)
    total = int(a['total'])
    waiting = max(0, total - int(a['done']) - int(a['exhausted']) - int(a['missing_username']))
    # Only the provider's terminal cursor (persisted as 'مكتمل') establishes fetch end.
    fetch_end = job['status'] == 'مكتمل'
    about_complete = (bool(a['enabled']) and int(a['done']) == total and waiting == 0
                      and int(a['exhausted']) == 0 and int(a['missing_username']) == 0)
    approved = fetch_end and about_complete
    if approved:
        reason = 'اكتمل سحب الصفحات وفحص About بنجاح لجميع المتابعين'
    elif not fetch_end:
        reason = 'لم يؤكد المصدر انتهاء صفحات المتابعين؛ التقرير أولي'
    elif not a['enabled']:
        reason = 'فحص About غير مفعل؛ التقرير أولي'
    elif waiting > 0:
        reason = f'توجد {waiting} فحوص About غير منتهية؛ التقرير أولي'
    else:
        reason = 'تعذر إكمال بعض فحوص About أو غاب اليوزر؛ التقرير أولي'
    return {'approved': approved, 'fetch_complete': fetch_end, 'about_complete': about_complete,
            'total': total, 'about_done': int(a['done']), 'about_pending': waiting,
            'about_exhausted': int(a['exhausted']), 'about_missing_username': int(a['missing_username']),
            'with_country': int(a['with_country']), 'reason': reason, 'target': job['target']}


def inspect_group(db, group_id):
    from multi_accounts import members
    rows = members(db, group_id)
    if not rows:
        return {'approved': False, 'reason': 'لا توجد حسابات ضمن المجموعة', 'members': []}
    results = [dict(job_id=r['job_id'], **inspect_job(db, r['job_id'])) for r in rows]
    approved = all(r['approved'] for r in results)
    return {'approved': approved, 'reason': ('جميع الحسابات مؤهلة للاعتماد' if approved
            else f'يوجد {sum(not r["approved"] for r in results)} حسابات لم تستوفِ شروط الاعتماد'),
            'members': results}
