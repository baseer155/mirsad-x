"""Excel output: complete URL text (not hyperlinks); permanent IDs as text."""
import io
from collections import Counter,defaultdict
from datetime import datetime
import xlsxwriter
from report_fields import account_links, account_status, connected_via, elapsed_metrics, fmt_duration
from filtering import exportable_rows

HEADERS=['الاسم','اليوزر','المعرف الدائم','رابط المعرف الدائم','رابط الحساب','رابط حول الحساب','الحساب محمي','موثق','عدد المتابعين','عدد المتابَعين','وقت المعالجة UTC','دولة الحساب','متصل عبر','حالة الحساب']

def clean_cell(value):
    if value is None:return ''
    value=str(value)
    # Prevent formula injection in spreadsheet viewers.
    if value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):return "'"+value
    return value

def make_excel(rows,events,job,audit=None):
    rows=exportable_rows(rows)
    output=io.BytesIO()
    book=xlsxwriter.Workbook(output,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False})
    book.set_properties({'title':'تقرير الحسابات حسب الدولة','comments':'الدولة حسب معلومات حول الحساب'})
    title=book.add_format({'bold':True,'font_color':'white','bg_color':'17384A','align':'right','valign':'vcenter','text_wrap':True,'border':0})
    plain=book.add_format({'align':'right','valign':'vcenter'})
    textfmt=book.add_format({'num_format':'@','align':'left'})
    url_text=book.add_format({'num_format':'@','align':'left','valign':'vcenter','font_color':'#17384A'})
    percent=book.add_format({'num_format':'0.0%','align':'center'})
    def sheet(name,headers,widths=None):
        ws=book.add_worksheet(name)
        ws.right_to_left();ws.freeze_panes(1,0);ws.set_row(0,30)
        for idx,h in enumerate(headers):ws.write_string(0,idx,h,title)
        for idx,width in enumerate(widths or []):ws.set_column(idx,idx,width)
        return ws
    w=sheet('المتابعون',HEADERS,[25,21,25,49,39,44,16,12,18,19,25,24,20,22])
    distribution=Counter()
    protected=defaultdict(lambda:[0,0])
    for i,r in enumerate(rows,1):
        uid=str(r['uid']);username=str(r.get('username') or '')
        country=r['account_country'];distribution[country]+=1
        protected[country][1]+=1
        if r.get('protected')==1:protected[country][0]+=1
        perm_url, profile_url, about_url = account_links(r)
        cols=[r.get('name'),('@'+username if username else ''),uid,
              perm_url,profile_url,about_url,
              'نعم' if r.get('protected')==1 else ('لا' if r.get('protected')==0 else 'غير متاح'),
              'نعم' if r.get('verified')==1 else ('لا' if r.get('verified')==0 else 'غير متاح'),
              r.get('followers_count'),r.get('following_count'),r.get('fetched_at'),
              r.get('account_country') or 'لم يتحقق',connected_via(r),account_status(r)]
        for j,v in enumerate(cols):
            if j in (3,4,5):
                # Visible complete URL, stored as plain text, with no Excel hyperlink relationship.
                w.write_string(i,j,clean_cell(v),url_text)
            elif j==1:w.write_string(i,j,('@'+username if username else ''),textfmt)
            elif j==2:w.write_string(i,j,uid,textfmt)
            elif j in (8,9) and v is not None:
                try:w.write_number(i,j,int(v),plain)
                except (ValueError,TypeError):w.write_string(i,j,clean_cell(v),plain)
            else:w.write_string(i,j,clean_cell(v),plain)
    w.autofilter(0,0,len(rows),len(HEADERS)-1)
    geo=sheet('تحليل الدول',['دولة الحساب','عدد الحسابات','النسبة المئوية'],[28,19,22])
    all_count=max(1,len(rows))
    for idx,(country,n) in enumerate(distribution.most_common(),1):
        geo.write_string(idx,0,country);geo.write_number(idx,1,n);geo.write_number(idx,2,n/all_count,percent)
    if distribution:
        chart=book.add_chart({'type':'bar'});chart.add_series({'name':'عدد الحسابات','categories':['تحليل الدول',1,0,len(distribution),0],'values':['تحليل الدول',1,1,len(distribution),1]})
        chart.set_title({'name':'التوزيع حسب دولة الحساب'});chart.set_legend({'none':True});chart.set_size({'width':700,'height':390});geo.insert_chart('E2',chart)
    pr=sheet('الحسابات المحمية',['دولة الحساب','الحسابات المحمية','كل الحسابات','نسبة المحمية'],[29,25,21,22])
    for i,(country,(p,total)) in enumerate(sorted(protected.items(),key=lambda kv:-kv[1][0]),1):
        pr.write_string(i,0,country);pr.write_number(i,1,p);pr.write_number(i,2,total);pr.write_number(i,3,p/total if total else 0,percent)
    ev=sheet('الخط الزمني',['الوقت UTC','الحدث','تمت معالجته','حسابات فريدة','التفاصيل'],[28,18,19,18,64])
    for i,item in enumerate(events,1):
        for j,key in enumerate(('at','kind','scanned','unique_count','details')):
            value=item.get(key)
            if j in (2,3):ev.write_number(i,j,int(value or 0))
            else:ev.write_string(i,j,clean_cell(value))
    metrics=elapsed_metrics(job, events)
    timeline_row=len(events)+3
    ev.write_string(timeline_row,0,'مدة طلبات سحب المتابعين',title)
    ev.write_string(timeline_row,1,fmt_duration(metrics['api_seconds']),plain)
    ev.write_string(timeline_row+1,0,'مدة المهمة منذ الإنشاء إلى آخر تحديث',title)
    ev.write_string(timeline_row+1,1,fmt_duration(metrics['wall_seconds']),plain)
    ev.write_string(timeline_row+2,0,'مدة إثراء About الدقيقة',title)
    ev.write_string(timeline_row+2,1,'غير مسجلة منفصلًا في النسخة الحالية',plain)
    ev.write_string(timeline_row+3,0,'متوسط مدة طلب سحب صفحة',title)
    ev.write_string(timeline_row+3,1,fmt_duration(metrics['seconds_per_page']),plain)
    if audit is not None:
        from report_audit import audit_lines
        aw=sheet('التحقق من التقرير',['المؤشر','القيمة'],[43,100])
        for i,(label,value) in enumerate(audit_lines(audit),1):
            aw.write_string(i,0,str(label),plain)
            aw.write_string(i,1,clean_cell(value),plain)
        aw.write_string(len(audit_lines(audit))+3,0,'توضيح',title)
        aw.write_string(len(audit_lines(audit))+3,1,'حالة الاعتماد تمثل اكتمال المعالجة وفق السجلات المتاحة، وليست ضمانًا لصحة جميع بيانات المصدر.',plain)
    book.close();return output.getvalue()


# Cross-target overlap for a group. Country must originate from About only.
def make_group_excel(db, group_id, audit=None):
    # Shared user matched by UID; only About-confirmed country included.
    from itertools import combinations
    group = db.execute('SELECT * FROM target_groups WHERE id=?',(int(group_id),)).fetchone()
    if not group:
        raise ValueError('المجموعة غير موجودة')
    members = db.execute('''SELECT j.id, j.target, j.target_uid, j.pages, j.elapsed_seconds,
        (SELECT COUNT(*) FROM followers WHERE job_id=j.id) AS total
        FROM target_group_members m JOIN jobs j ON j.id=m.job_id
        WHERE m.group_id=? ORDER BY m.ordinal''',(int(group_id),)).fetchall()
    if not members:
        raise ValueError('المجموعة دون حسابات')
    ids = [int(m['id']) for m in members]
    # Only parameterized SQL, even if group contains 100 targets.
    placeholders = ','.join('?' for _ in ids)
    linked = db.execute(f'''SELECT job_id, CAST(uid AS TEXT) uid, username, name,
        account_country, connected_via FROM followers WHERE job_id IN ({placeholders})''',ids).fetchall()
    by_user = defaultdict(dict)
    per_target = defaultdict(set)
    for item in linked:
        country = str(item['account_country'] or '').strip()
        if not country or country.lower() in ('none','null','غير متاح','دولة الحساب غير متاح','لم يتحقق'):
            continue
        uid = str(item['uid'] or '').strip()
        if not uid:
            continue
        job_id = int(item['job_id'])
        by_user[uid][job_id] = dict(item)
        per_target[job_id].add(uid)
    target_name = {int(m['id']):'@'+str(m['target']).lstrip('@') for m in members}
    shared = [(uid, links) for uid, links in by_user.items() if len(links)>=2]
    shared.sort(key=lambda kv:(-len(kv[1]),kv[0]))
    out = io.BytesIO()
    wb = xlsxwriter.Workbook(out,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False})
    wb.set_properties({'title':'تحليل الحسابات المشتركة بين الأهداف'})
    fmt_h = wb.add_format({'bold':True,'font_color':'white','bg_color':'17384A','align':'right','valign':'vcenter','text_wrap':True})
    fmt_txt = wb.add_format({'num_format':'@','align':'left','valign':'vcenter'})
    fmt_n = wb.add_format({'align':'center','valign':'vcenter','num_format':'#,##0'})
    fmt_pct = wb.add_format({'align':'center','num_format':'0.0%'})
    def sheet(name,headers,widths):
        ws=wb.add_worksheet(name);ws.right_to_left();ws.freeze_panes(1,0)
        ws.set_row(0,38)
        for i,h in enumerate(headers):ws.write_string(0,i,h,fmt_h)
        for i,w in enumerate(widths):ws.set_column(i,i,w)
        return ws
    summary = sheet('ملخص المجموعة',['الحساب المستهدف','المعرف الدائم','إجمالي المسحوب','حسابات ذات دولة About','حسابات مشتركة','صفحات السحب','زمن API بالثواني'],[28,24,22,25,20,19,25])
    for i,m in enumerate(members,1):
        jid = int(m['id'])
        overlap = sum(1 for uid in per_target[jid] if len(by_user[uid])>=2)
        vals=[target_name[jid],str(m['target_uid']),int(m['total']),sum(bool(by_user[uid][jid].get('account_country')) for uid in per_target[jid]),overlap,int(m['pages']),float(m['elapsed_seconds'] or 0)]
        for j,v in enumerate(vals):
            if j in (0,1):summary.write_string(i,j,str(v),fmt_txt)
            else:summary.write_number(i,j,v,fmt_n)
    summary.autofilter(0,0,len(members),6)
    w=sheet('الحسابات المكررة',['اسم المتابع','يوزر المتابع','المعرف الدائم','دولة الحساب','متصل عبر','عدد الحسابات المستهدفة','اليوزرات المستهدفة','روابط الحسابات المستهدفة','رابط المتابع','رابط المعرف الدائم','رابط حول الحساب'],[29,24,26,23,24,24,75,100,45,55,52])
    for i,(uid,links) in enumerate(shared,1):
        records=list(links.values()); display=next((r for r in records if r.get('username')),records[0]); uname=str(display.get('username') or '').lstrip('@')
        display_name=next((r.get('name') for r in records if r.get('name')),'')
        countries=sorted({str(r.get('account_country') or '').strip() for r in records if r.get('account_country')})
        channels=sorted({str(r.get('connected_via') or '').strip() for r in records if r.get('connected_via')})
        ordered=[m for m in members if int(m['id']) in links]
        handles=[target_name[int(m['id'])] for m in ordered]
        links_text=' | '.join('https://x.com/'+h.lstrip('@') for h in handles)
        cells=[display_name, '@'+uname if uname else '', uid, '، '.join(countries), '، '.join(channels) or 'غير متاح من المصدر',len(handles), '، '.join(handles),links_text,
               'https://x.com/'+uname if uname else '', 'https://x.com/i/user/'+uid,
               'https://x.com/'+uname+'/about' if uname else '']
        for j,v in enumerate(cells):
            if j==5:w.write_number(i,j,v,fmt_n)
            else:w.write_string(i,j,clean_cell(v),fmt_txt)
        if i%2==0:w.set_row(i,24)
    w.autofilter(0,0,len(shared),10)
    cross=sheet('تداخل الحسابات',['الحساب المستهدف الأول','الحساب المستهدف الثاني','متابعون مشتركون','حسابات الأول المسحوبة','حسابات الثاني المسحوبة','نسبة التداخل من الأصغر'],[32,32,23,32,32,29])
    idx=0
    for a,b in combinations(members,2):
        aid,bid=int(a['id']),int(b['id'])
        intersection=len(per_target[aid] & per_target[bid]);idx+=1
        for j,value in enumerate([target_name[aid],target_name[bid],intersection,len(per_target[aid]),len(per_target[bid])]):
            if j<2:cross.write_string(idx,j,value,fmt_txt)
            else:cross.write_number(idx,j,value,fmt_n)
        smaller=min(len(per_target[aid]),len(per_target[bid]))
        cross.write_number(idx,5,intersection/smaller if smaller else 0,fmt_pct)
    cross.autofilter(0,0,max(1,idx),5)
    if idx:
        chart=wb.add_chart({'type':'bar'})
        chart.add_series({'name':'عدد الحسابات المشتركة','categories':['تداخل الحسابات',1,1,min(idx,12),1],
            'values':['تداخل الحسابات',1,2,min(idx,12),2]})
        chart.set_legend({'none':True});chart.set_title({'name':'التداخل بين الحسابات المستهدفة'})
        chart.set_size({'width':690,'height':360});cross.insert_chart('H2',chart)
    if audit is not None:
        from report_audit import audit_lines
        audit_sheet=sheet('التحقق من التقرير',['المؤشر','القيمة'],[48,95])
        audit_sheet.write_string(1,0,'حالة المجموعة',fmt_txt)
        audit_sheet.write_string(1,1,str(audit['status']),fmt_txt)
        for idx,(label,value) in enumerate(audit_lines(audit),2):
            audit_sheet.write_string(idx,0,str(label),fmt_txt)
            audit_sheet.write_string(idx,1,clean_cell(value),fmt_txt)
        member_sheet=sheet('اعتماد الحسابات المستهدفة',['الحساب المستهدف','المهمة','حالة التقرير','المسحوب','المؤهل','المستبعد','فحوص About الناجحة','معلقة','السبب'],[32,16,23,18,18,18,25,15,78])
        for i,(item,job) in enumerate(zip(audit['members'],members),1):
            values=['@'+str(job['target']).lstrip('@'),str(item['scope_id']),item['status'],item['total_followers'],item['exported'],item['excluded_without_country'],item['about_done'],item['about_pending'],item['reason']]
            for j,value in enumerate(values):
                if j in (3,4,5,6,7):member_sheet.write_number(i,j,int(value),fmt_n)
                else:member_sheet.write_string(i,j,clean_cell(value),fmt_txt)
    wb.close()
    return out.getvalue()
