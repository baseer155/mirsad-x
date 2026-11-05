"""Strict, shared export policy: include only rows with confirmed account_country.
Does not modify the database or live app preview.
"""
from __future__ import annotations

UNKNOWN = {
    '', 'none', 'null', 'nan', 'n/a', 'na', 'unknown', 'not available',
    'unavailable', 'not checked', 'not verified', 'not provided',
    'غير متاح', 'غير متاح من المصدر', 'غير محدد', 'غير محددة',
    'لم يُفحص بعد', 'لم يفحص بعد', 'لم يتحقق', 'غير معروف', 'لا يوجد',
    'غير متوفرة', 'غير متوفر', '-', '--'
}

def normalized_country(value):
    if not isinstance(value, str):
        return ''
    v = value.strip()
    return v if v.casefold() not in UNKNOWN else ''

def exportable_rows(rows):
    return [dict(r, account_country=normalized_country(r.get('account_country')))
            for r in rows if normalized_country(r.get('account_country'))]
