import io
import logging
from datetime import datetime

try:
    from weasyprint import HTML, CSS
    WEASYPRINT_INSTALLED = True
except (ImportError, OSError):
    # OSError: Python package installed but native Pango/Cairo libraries missing
    WEASYPRINT_INSTALLED = False

logger = logging.getLogger('ats_resume_scorer')

def generate_combined_pdf(html_docs: dict[str, str]) -> bytes:
    if not WEASYPRINT_INSTALLED:
        raise ImportError("WeasyPrint is not installed. PDF generation unavailable.")
        
    documents = []
    
    # Render all 3 HTML strings to WeasyPrint Document objects
    for name, html_str in html_docs.items():
        doc = HTML(string=html_str).render()
        documents.append(doc)
    
    # Merge them into the first document
    first_doc = documents[0]
    for other_doc in documents[1:]:
        for page in other_doc.pages:
            first_doc.pages.append(page)
            
    # Write combined PDF bytes
    pdf_bytes = first_doc.write_pdf()
    return pdf_bytes


# ---------------------------------------------------------------------------
# Pure-Python fallback (reportlab) – works on Windows with just `pip install`
# ---------------------------------------------------------------------------

def _clean(value) -> str:
    """Make text safe for reportlab's built-in Helvetica font and XML-escape it."""
    from xml.sax.saxutils import escape
    text = '' if value is None else str(value)
    text = text.replace('\u2192', '->').replace('\u2022', '-').replace('\u2013', '-').replace('\u2014', '-')
    text = text.encode('cp1252', errors='replace').decode('cp1252')  # drop emoji etc.
    return escape(text)


def _as_dict(obj):
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    return getattr(obj, '__dict__', {})


def generate_fallback_pdf(analysis_data: dict) -> bytes:
    """Build the ATS report with reportlab (no system libraries required)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

    data = _as_dict(analysis_data)
    score = float(data.get('ATS_score') or data.get('ats_score') or 0)
    score_color = colors.HexColor('#16a34a' if score >= 80 else '#d97706' if score >= 60 else '#dc2626')

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=ss['Title'], fontSize=22, spaceAfter=6, alignment=0)
    h2 = ParagraphStyle('h2', parent=ss['Heading2'], fontSize=14, spaceBefore=14, spaceAfter=6,
                        textColor=colors.HexColor('#3730a3'))
    h3 = ParagraphStyle('h3', parent=ss['Heading3'], fontSize=11, spaceBefore=8, spaceAfter=2)
    body = ParagraphStyle('body', parent=ss['BodyText'], fontSize=10, leading=14)
    small = ParagraphStyle('small', parent=body, fontSize=9, textColor=colors.HexColor('#4b5563'))
    big = ParagraphStyle('big', parent=ss['Title'], fontSize=44, leading=54, alignment=0,
                     textColor=score_color, spaceAfter=6)

    story = [Paragraph('ATS Resume Analysis Report', h1),
             Paragraph(_clean(datetime.now().strftime('Generated on %B %d, %Y')), small),
             Spacer(1, 8 * mm),
             Paragraph(f'{score:.1f} / 100', big),
             Paragraph(_clean(data.get('interpretation') or ''), body)]

    # Component scores
    cs = _as_dict(data.get('component_scores'))
    rows = [['Component', 'Score', 'Max']]
    for key, label, mx in [('formatting', 'Formatting', 20), ('keywords', 'Keywords', 25),
                           ('content', 'Content Quality', 25), ('skill_validation', 'Skill Validation', 15),
                           ('ats_compatibility', 'ATS Compatibility', 15)]:
        rows.append([label, f"{float(cs.get(key, 0) or 0):.1f}", str(mx)])
    table = Table(rows, colWidths=[90 * mm, 30 * mm, 30 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3730a3')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3f4f6')]),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story += [Paragraph('Score Breakdown', h2), table]

    # Strengths
    strengths = data.get('strengths') or []
    if strengths:
        story.append(Paragraph('Strengths', h2))
        story += [Paragraph('- ' + _clean(s), body) for s in strengths]

    # Skill validation
    svd = _as_dict(data.get('skill_validation_details'))
    if svd and (svd.get('validated') or svd.get('unvalidated')):
        story.append(Paragraph('Skill Validation', h2))
        story.append(Paragraph(
            f"{svd.get('validated_count', 0)} of {svd.get('total', 0)} skills are backed by a project "
            f"({float(svd.get('validation_pct', 0) or 0):.0f}%).", body))
        for item in svd.get('validated', []):
            item = _as_dict(item)
            projects = ', '.join(item.get('projects', []) or [])
            story.append(Paragraph(f"- <b>{_clean(item.get('skill'))}</b> - {_clean(projects)}", body))
        if svd.get('unvalidated'):
            story.append(Paragraph('Not demonstrated in any project: ' + _clean(', '.join(svd['unvalidated'])), body))

    # Job description comparison
    jd = _as_dict(data.get('jd_match_analysis') or data.get('jd_comparison'))
    if jd:
        story.append(Paragraph('Job Description Match', h2))
        story.append(Paragraph(
            f"Match: <b>{float(jd.get('match_percentage', 0) or 0):.0f}%</b> &nbsp;|&nbsp; "
            f"Semantic similarity: <b>{float(jd.get('semantic_similarity', 0) or 0):.0f}%</b>", body))
        for label, key in [('Matched keywords', 'matched_keywords'), ('Missing keywords', 'missing_keywords'),
                           ('Skills gap', 'skills_gap')]:
            if jd.get(key):
                story.append(Paragraph(f'<b>{label}:</b> ' + _clean(', '.join(jd[key])), body))

    # Detailed feedback
    feedback = [_as_dict(f) for f in (data.get('detailed_feedback') or [])]
    if feedback:
        story.append(PageBreak())
        story.append(Paragraph('Detailed Feedback', h1))
        order = {'critical': 0, 'high': 1, 'moderate': 2, 'medium': 2, 'low': 3}
        sev_color = {'critical': '#991b1b', 'high': '#dc2626', 'moderate': '#d97706',
                     'medium': '#d97706', 'low': '#16a34a'}
        for fb in sorted(feedback, key=lambda f: order.get(str(f.get('severity_level', '')).lower(), 9)):
            sev = str(fb.get('severity_level', '')).lower()
            block = [Paragraph(
                f"{_clean(fb.get('issue_title'))} "
                f"<font color='{sev_color.get(sev, '#4b5563')}' size='9'>[{_clean(sev.upper())}]</font>", h3)]
            for label, key in [('ATS impact', 'ats_impact'), ('Why it matters', 'explanation'),
                               ('Where', 'where_it_appears'), ('How to fix', 'how_to_fix'),
                               ('Example', 'example_improvement')]:
                if fb.get(key):
                    block.append(Paragraph(f'<b>{label}:</b> {_clean(fb[key])}', body))
            for action in fb.get('action_items') or []:
                block.append(Paragraph('&nbsp;&nbsp;- ' + _clean(action), body))
            block.append(Spacer(1, 4))
            story.append(KeepTogether(block))

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm,
                      title='ATS Resume Analysis Report').build(story)
    return buf.getvalue()


def generate_report_pdf(html_docs: dict, analysis_data: dict) -> bytes:
    """Use WeasyPrint (nicest output) when it works, otherwise fall back to reportlab."""
    if WEASYPRINT_INSTALLED:
        try:
            return generate_combined_pdf(html_docs)
        except Exception as e:
            logger.warning(f'WeasyPrint failed ({e}); using reportlab fallback.')
    else:
        logger.info('WeasyPrint unavailable; using reportlab fallback for PDF export.')
    return generate_fallback_pdf(analysis_data)
