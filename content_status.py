"""Conservative status labels for inaccessible posts and protected accounts."""

def _bool(value):
    if value is True or value == 1 or (isinstance(value, str) and value.strip().lower() in ('true','1','yes')):
        return True
    if value is False or value == 0 or (isinstance(value, str) and value.strip().lower() in ('false','0','no')):
        return False
    return None


def posts_status(row):
    """Do not infer a protected account from missing API data alone."""
    protected = _bool(row.get('protected'))
    if protected is True:
        return 'هذه المنشورات محميّة'
    if protected is False:
        return 'الحساب غير محمي (لم يتم التحقق من توفر المنشورات)'
    return 'تعذّر التحقق من حالة المنشورات'
