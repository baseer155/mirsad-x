"""Editable Arabic Word and PowerPoint reports for Mirsad. No external API calls."""
from __future__ import annotations
from collections import Counter
from filtering import exportable_rows
from report_fields import account_status, connected_via, elapsed_metrics, fmt_duration, account_links
from datetime import datetime, timezone
from io import BytesIO
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from math import ceil
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pptx import Presentation
from pptx.util import Inches, Pt as PPTPt
from pptx.dml.color import RGBColor as PPTColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

NAVY = (14,43,59)
TEAL = (16,147,137)
LIGHT = (234,246,244)
GREY = (96,113,124)


def _s(value):
    if value is None:return ''
    return str(value).replace('\x00','').strip()


def _job(job, name, default=''):
    return _s(job.get(name,default))


def _summary(rows, events, job):
    rows=exportable_rows(rows)
    total=len(rows)
    countries=Counter(_s(r.get('account_country')) or 'غير متاح من المصدر' for r in rows)
    known={k:v for k,v in countries.items() if k not in ('غير متاح من المصدر','غير محددة','غير محدد','لم يُفحص بعد','None')}
    named=sum(bool(_s(r.get('name'))) for r in rows)
    handles=sum(bool(_s(r.get('username'))) for r in rows)
    protected=sum(r.get('protected') in (1,True,'1') for r in rows)
    about=sum(bool(_s(r.get('account_country'))) and _s(r.get('account_country')) not in ('None','لم يُفحص بعد','غير متاح من المصدر') for r in rows)
    return dict(total=total,known=known,unknown=total-sum(known.values()),named=named,handles=handles,protected=protected,about=about,
                pages=int(job.get('pages') or 0),seconds=float(job.get('elapsed_seconds') or 0),
                target=_job(job,'target'),platform=_job(job,'platform'),status=_job(job,'status'),events=events)


def _rtl(p):
    p.alignment=WD_ALIGN_PARAGRAPH.RIGHT
    ppr=p._p.get_or_add_pPr()
    bidi=OxmlElement('w:bidi');bidi.set(qn('w:val'),'1');ppr.append(bidi)


def _font(run,size=10,bold=False,color=NAVY):
    run.bold=bold;run.font.name='Arial';run.font.size=Pt(size);run.font.color.rgb=RGBColor(*color)
    rpr=run._r.get_or_add_rPr();rf=OxmlElement('w:rFonts');rf.set(qn('w:cs'),'Arial');rf.set(qn('w:eastAsia'),'Arial');rpr.append(rf)
    rtl=OxmlElement('w:rtl');rtl.set(qn('w:val'),'1');rpr.append(rtl)


def _para(doc,text,size=11,bold=False,color=NAVY):
    p=doc.add_paragraph();_rtl(p);_font(p.add_run(_s(text)),size,bold,color);return p


def _section(doc,title):
    p=_para(doc,title,15,True,TEAL);p.paragraph_format.space_before=Pt(16);p.paragraph_format.space_after=Pt(6)


def _table(doc, headers, rows, widths=None):
    t=doc.add_table(rows=1,cols=len(headers));t.style='Light Shading Accent 1';t.alignment=WD_TABLE_ALIGNMENT.CENTER
    for j,h in enumerate(headers):
        cell=t.rows[0].cells[j];cell.text='';p=cell.paragraphs[0];_rtl(p);_font(p.add_run(h),9,True)
    for rec in rows:
        cells=t.add_row().cells
        for j,v in enumerate(rec):
            cells[j].text='';p=cells[j].paragraphs[0];_rtl(p);_font(p.add_run(_s(v)),9)
    for row in t.rows:
        for c in row.cells:c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
    return t


def _geo_figure(known):
    """White-background neutral horizontal chart, sized for a report page."""
    items=sorted(known.items(),key=lambda x:-x[1])[:9]
    fig,ax=plt.subplots(figsize=(9,4.5),dpi=150)
    fig.patch.set_facecolor('white');ax.set_facecolor('white')
    if items:
        ys=list(range(len(items)))
        ax.barh(ys,[v for k,v in items],color='#109389',height=.58)
        ax.set_yticks(ys,[k for k,v in items]);ax.invert_yaxis()
        ax.spines[['top','right','left']].set_visible(False)
        ax.grid(axis='x',alpha=.15);ax.set_axisbelow(True)
        for i,(_,v) in enumerate(items):ax.text(v,i,f'  {v:,}',ha='left',va='center',fontsize=9,color='#17384a')
        ax.set_xlim(0,max(v for _,v in items)*1.22)
    else:
        ax.text(.5,.5,'No verified account countries',ha='center',va='center',transform=ax.transAxes)
        ax.axis('off')
    fig.tight_layout(pad=1.3)
    f=BytesIO();fig.savefig(f,format='png',facecolor='white',bbox_inches='tight');plt.close(fig)
    f.seek(0);return f


def _ppt_native_geo_chart(slide, known):
    items=sorted(known.items(),key=lambda x:-x[1])[:9]
    if not items:
        _ppt_text(slide,1.5,2.5,10.5,.8,'لا توجد حسابات مؤهلة للإدراج',22)
        return
    data=CategoryChartData()
    data.categories=[country for country,count in items]
    data.add_series('عدد الحسابات',[v for country,v in items])
    chart=slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(1.0), Inches(1.25), Inches(11.35), Inches(5.65),data).chart
    chart.has_legend=False
    chart.has_title=False
    chart.chart_style=10
    chart.plots[0].has_data_labels=True
    chart.plots[0].data_labels.position=XL_LABEL_POSITION.OUTSIDE_END
    chart.value_axis.has_major_gridlines=True
    chart.value_axis.minimum_scale=0
    chart.value_axis.maximum_scale=max(1,max(v for _,v in items))
    chart.value_axis.major_unit=1 if max(v for _,v in items)<=20 else max(1,round(max(v for _,v in items)/5))
    chart.series[0].format.fill.solid()
    chart.series[0].format.fill.fore_color.rgb=PPTColor(*TEAL)
    chart.category_axis.reverse_order=True
    return chart


def _safe_filename(s):return re.sub(r'[^\w\-]+','_',_s(s))[:50] or 'report'


def make_word(rows,events,job):
    d=Document();sec=d.sections[0];sec.top_margin=Cm(1.8);sec.bottom_margin=Cm(1.8);sec.left_margin=Cm(1.8);sec.right_margin=Cm(1.8)
    styles=d.styles;styles['Normal'].font.name='Arial';styles['Normal'].font.size=Pt(10)
    rows=exportable_rows(rows)
    summ=_summary(rows,events,job)
    _para(d,'التقرير التنفيذي لتحليل الحسابات',17,True)
    _para(d,'منصة: '+summ['platform']+'  |  الحساب المستهدف: '+summ['target'],11)
    _para(d,'تاريخ إصدار التقرير: '+datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),9,color=GREY)
    _section(d,'الملخص التنفيذي')
    _para(d,f"يتضمن هذا التقرير {summ['total']:,} حسابًا فريدًا محفوظًا. تم رصد دولة الحساب من صفحة «حول الحساب» لعدد {summ['about']:,} حسابًا، ولا تُفترض الدولة عند غياب معلومة صريحة.")
    _table(d,['المؤشر','القيمة'],[
        ('الحسابات الفريدة',f"{summ['total']:,}"),('الأسماء المتاحة',f"{summ['named']:,}"),('اليوزرات المتاحة',f"{summ['handles']:,}"),
        ('الدول المحددة',str(len(summ['known']))),('الحسابات المحمية المؤكدة',f"{summ['protected']:,}"),
        ('الصفحات المسحوبة',str(summ['pages'])),('زمن طلبات سحب المتابعين',fmt_duration(summ['seconds']))])
    tm=elapsed_metrics(job,events)
    _para(d,'الزمن منذ إنشاء المهمة حتى آخر تحديث: '+fmt_duration(tm['wall_seconds']))
    _para(d,'مدة إثراء About منفصلة: غير مسجلة بدقة في بيانات هذه النسخة.',9,color=GREY)
    _section(d,'التوزيع الجغرافي حسب «حول الحساب»')
    if summ['known']:
        _table(d,['الدولة','عدد الحسابات','النسبة من الإجمالي'],[(k,str(v),f'{v/max(1,summ["total"])*100:.1f}%') for k,v in sorted(summ['known'].items(),key=lambda x:-x[1])[:25]])
    else:_para(d,'لا توجد حسابات مؤهلة للإدراج.',10,color=GREY)
    if summ['known']:
        _section(d,'الرسم البياني للتوزيع الجغرافي')
        d.add_picture(_geo_figure(summ['known']),width=Cm(16.5))
    _section(d,'عينة من الحسابات المحفوظة (حتى 30 حسابًا)')
    sample=[]
    for r in rows[:30]:
        u=_s(r.get('username'));uid=_s(r.get('uid'))
        sample.append((_s(r.get('name')) or 'غير متاح',('@'+u) if u else 'غير متاح',uid,_s(r.get('account_country')) or 'غير متاح'))
    if sample:_table(d,['الاسم','اليوزر','المعرف الدائم','دولة الحساب'],sample)
    else:_para(d,'لا توجد حسابات محفوظة.',10)
    _section(d,'حالة الحسابات (وفق بيانات المصدر)')
    statuses=Counter(account_status(r) for r in rows)
    _table(d,['الحالة','عدد الحسابات'],[(k,str(v)) for k,v in statuses.items()])
    _section(d,'الخط الزمني — آخر 12 حدثًا')
    if events:_table(d,['التوقيت UTC','الحدث','الفريد','التفاصيل'],[(_s(e.get('at')),_s(e.get('kind')),_s(e.get('unique_count')),_s(e.get('details'))[:110]) for e in events[-12:]])
    else:_para(d,'لا توجد أحداث مسجلة.',10)
    out=BytesIO();d.save(out);return out.getvalue()


def _ppt_text(slide,x,y,w,h,text,size=16,bold=False,color=NAVY,fill=None):
    if fill:
        shape=slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        shape.fill.solid();shape.fill.fore_color.rgb=PPTColor(*fill);shape.line.fill.background()
        tf=shape.text_frame
    else:tf=slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
    tf.clear();tf.word_wrap=True;tf.margin_left=Inches(.12);tf.margin_right=Inches(.12)
    p=tf.paragraphs[0];p.alignment=PP_ALIGN.RIGHT
    p.text=_s(text);p.font.name='Arial';p.font.size=PPTPt(size);p.font.bold=bold;p.font.color.rgb=PPTColor(*color)
    return tf


def _base_slide(pres,title,num):
    slide=pres.slides.add_slide(pres.slide_layouts[6]);background=slide.background.fill;background.solid();background.fore_color.rgb=PPTColor(255,255,255)
    _ppt_text(slide,.6,.3,12.1,.6,title,23,True)
    icon=slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(12.18), Inches(.43), Inches(.36), Inches(.36))
    icon.fill.solid();icon.fill.fore_color.rgb=PPTColor(*TEAL);icon.line.fill.background()
    return slide


def make_powerpoint(rows,events,job):
    rows=exportable_rows(rows)
    s=_summary(rows,events,job);p=Presentation();p.slide_width=Inches(13.333);p.slide_height=Inches(7.5)
    slide=p.slides.add_slide(p.slide_layouts[6]);slide.background.fill.solid();slide.background.fill.fore_color.rgb=PPTColor(255,255,255)
    bar=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(12.95), Inches(.65), Inches(.12), Inches(5.95))
    bar.fill.solid();bar.fill.fore_color.rgb=PPTColor(*TEAL);bar.line.fill.background()
    _ppt_text(slide,.8,1.6,11.7,1.3,'تقرير تحليل الحسابات',36,True,NAVY)
    _ppt_text(slide,.8,3.2,11.7,.8,'تقرير تنفيذي لتحليل الحسابات',25,False,TEAL)
    _ppt_text(slide,.8,5.6,11.7,.5,f"الحساب المستهدف: {s['target']}  |  {datetime.now(timezone.utc).strftime('%Y-%m-%d')} UTC",15,False,GREY)
    slide=_base_slide(p,'الملخص التنفيذي',2)
    metrics=[('حسابات فريدة',s['total']),('أسماء متاحة',s['named']),('دول محددة',len(s['known'])),('فحص دولة متاح',s['about']),('محمي مؤكد',s['protected']),('دول مختلفة',len(s['known']))]
    for i,(label,value) in enumerate(metrics):
        c=i%3;r=i//3;x=.7+c*4.16;y=1.5+r*2.1
        _ppt_text(slide,x,y,3.85,1.65,f'{value:,}\n{label}',24,True,TEAL,fill=LIGHT)
    slide=_base_slide(p,'الدول حسب صفحة «حول الحساب»',3)
    _ppt_native_geo_chart(slide,s['known'])
    slide=_base_slide(p,'جودة البيانات',4)
    qual=[('توفر الاسم',s['named']),('توفر اليوزر',s['handles']),('توفر دولة الحساب',s['about'])]
    for i,(label,num) in enumerate(qual):
        y=1.6+i*1.6
        _ppt_text(slide,9.2,y,3,.5,label,20,True)
        bg=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1.3), Inches(y+.60), Inches(10.4), Inches(.32));bg.fill.solid();bg.fill.fore_color.rgb=PPTColor(225,232,237);bg.line.fill.background()
        if num:
            fg=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(11.7-10.4*num/max(1,s['total'])), Inches(y+.60), Inches(10.4*num/max(1,s['total'])), Inches(.32));fg.fill.solid();fg.fill.fore_color.rgb=PPTColor(*TEAL);fg.line.fill.background()
        _ppt_text(slide,1.3,y,2,.4,f'{num:,} / {s["total"]:,}',15)
    slide=_base_slide(p,'حالة الحسابات',5)
    stat=Counter(account_status(r) for r in rows)
    for i,(label,count) in enumerate(stat.items()):
        _ppt_text(slide,1.2,1.45+i*1.05,10.5,.8,f'{label}: {count:,}',23,True,TEAL,fill=LIGHT)
    tm=elapsed_metrics(job,events)
    _ppt_text(slide,1.2,6.35,10.5,.45,'مدة طلبات السحب: '+fmt_duration(tm['api_seconds'])+' | مدة المهمة: '+fmt_duration(tm['wall_seconds']),14)
    slide=_base_slide(p,'عينة من الحسابات',6)
    for i,r in enumerate(rows[:8]):
        yy=1.35+i*.61;uid=_s(r.get('uid'));username=_s(r.get('username'));name=_s(r.get('name')) or 'غير متاح'
        bg=(234,246,244) if i%2==0 else (247,249,251)
        _ppt_text(slide,1,yy,11.3,.52,f'{name}  |  @{username if username else "غير متاح"}  |  {uid}',14,fill=bg)
    if events:
        slide=_base_slide(p,'الخط الزمني للتشغيل',7)
        for i,e in enumerate(events[-8:]):
            yy=1.25+i*.64;_ppt_text(slide,.8,yy,11.7,.52,f"{_s(e.get('at'))[:19]}  |  {_s(e.get('kind'))}  |  {_s(e.get('details'))[:80]}",12,fill=LIGHT if i%2==0 else (247,249,251))
    out=BytesIO();p.save(out);return out.getvalue()
