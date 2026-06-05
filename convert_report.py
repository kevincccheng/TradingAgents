#!/usr/bin/env python3
"""
convert_report.py
Converts TradingAgents complete_report.md to a professional PDF.
Usage:
    python convert_report.py                     # auto-finds latest report
    python convert_report.py path/to/report.md   # specific file
"""
import os, sys, re, glob, shutil
from pathlib import Path
from datetime import datetime


# ── Text cleaning ────────────────────────────────────────────────────────────

_REPLACE = [
    ('✅','[OK]'), ('❌','[NO]'), ('✗','[NO]'), ('✓','[OK]'),
    ('⚠️','[!]'), ('⚠','[!]'), ('✔','[OK]'),
    ('→','->'), ('←','<-'), ('↑','^'), ('↓','v'),
    ('★','*'), ('☆','*'), ('•','-'),
    ('‘',"'"), ('’',"'"), ('“','"'), ('”','"'),
    ('–','-'), ('—','--'), ('…','...'), (' ',' '),
    ('×','x'), ('÷','/'), ('≈','~'), ('≥','>='),
    ('≤','<='), ('°','deg'), ('±','+-'),
]

def clean(s: str) -> str:
    for src, dst in _REPLACE:
        s = s.replace(src, dst)
    return re.sub(r'[^\x09\x0a\x0d\x20-\x7e\xa1-\xff]', '', s)

def esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def md2rl(s: str) -> str:
    s = esc(s)
    s = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', s)
    s = re.sub(r'\*\*(.+?)\*\*',     r'<b>\1</b>', s)
    s = re.sub(r'\*([^*\n]+?)\*',    r'<i>\1</i>', s)
    s = re.sub(r'`([^`\n]+?)`',
               r'<font name="Courier" fontSize="8">\1</font>', s)
    return s


# ── File discovery ────────────────────────────────────────────────────────────

def find_report(base: Path) -> Path:
    found = []
    for pat in [base / 'outputs' / '**' / 'complete_report.md',
                base / 'reports' / '**' / 'complete_report.md']:
        found += glob.glob(str(pat), recursive=True)
    if not found:
        sys.exit(
            'ERROR: No complete_report.md found.\n'
            'Run TradingAgents and press Y at the "Save report?" prompt.')
    return Path(max(found, key=os.path.getmtime))

def get_meta(content: str):
    m = re.search(r'#\s*Trading Analysis Report:\s*(.+)', content)
    ticker = m.group(1).strip() if m else 'UNKNOWN'
    m = re.search(r'Generated:\s*(\d{4}-\d{2}-\d{2})', content)
    date = m.group(1) if m else datetime.now().strftime('%Y-%m-%d')
    return ticker, date


# ── Agent colour table ────────────────────────────────────────────────────────

_AGENT_THEME = {
    'Market Analyst':       ('#154360', '#FFFFFF'),
    'Sentiment Analyst':    ('#154360', '#FFFFFF'),
    'News Analyst':         ('#154360', '#FFFFFF'),
    'Fundamentals Analyst': ('#154360', '#FFFFFF'),
    'Bull Researcher':      ('#145A32', '#FFFFFF'),
    'Bear Researcher':      ('#78281F', '#FFFFFF'),
    'Research Manager':     ('#212F3D', '#FFFFFF'),
    'Trader':               ('#6E2F09', '#FFFFFF'),
    'Aggressive Analyst':   ('#512E5F', '#FFFFFF'),
    'Conservative Analyst': ('#1A5276', '#FFFFFF'),
    'Neutral Analyst':      ('#1C2833', '#FFFFFF'),
    'Portfolio Manager':    ('#4D3900', '#FFF9C4'),
}

def agent_theme(name: str):
    for key, colours in _AGENT_THEME.items():
        if key.lower() in name.lower():
            return colours
    return ('#343A40', '#FFFFFF')


# ── PDF builder ───────────────────────────────────────────────────────────────

def build_pdf(report_path: Path) -> Path:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, HRFlowable,
        )
    except ImportError:
        sys.exit('reportlab not installed. Run setup.bat or: pip install reportlab')

    raw = report_path.read_text(encoding='utf-8', errors='replace')
    content = clean(raw)
    ticker, date = get_meta(content)

    safe_ticker = re.sub(r'[\\/:*?"<>|]', '_', ticker)
    pdf_path = report_path.parent / f'{safe_ticker}_{date}.pdf'

    script_dir = Path(__file__).parent
    latest = script_dir / 'outputs' / 'latest_report.pdf'
    latest.parent.mkdir(exist_ok=True)

    # Colours
    NAVY  = colors.HexColor('#1B2B4B')
    GOLD  = colors.HexColor('#C09B3A')
    GRAY  = colors.HexColor('#6C757D')
    LGRAY = colors.HexColor('#F8F9FA')
    MGRAY = colors.HexColor('#CED4DA')
    DGRAY = colors.HexColor('#343A40')

    # Page callbacks
    def draw_footer(canvas, doc):
        canvas.saveState()
        w, _ = letter
        canvas.setStrokeColor(MGRAY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75 * inch, 0.65 * inch, w - 0.75 * inch, 0.65 * inch)
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75 * inch, 0.45 * inch,
                          f'Investment Research Report  |  {ticker}')
        canvas.drawRightString(w - 0.75 * inch, 0.45 * inch,
                               f'{date}  |  Page {doc.page}')
        canvas.restoreState()

    # Style factory (ParagraphStyle does not register globally — safe to repeat)
    def ps(name, **kw):
        from reportlab.lib.styles import getSampleStyleSheet
        parent = kw.pop('parent', getSampleStyleSheet()['Normal'])
        return ParagraphStyle(name, parent=parent, **kw)

    body   = ps('body',   fontSize=9.5, leading=14, spaceAfter=6,
                alignment=TA_JUSTIFY, textColor=colors.HexColor('#212529'))
    blt    = ps('blt',    parent=body,  alignment=TA_LEFT,
                leftIndent=18, firstLineIndent=0, spaceAfter=3)
    nblt   = ps('nblt',   parent=blt,   leftIndent=34)
    sec_h  = ps('sec_h',  fontSize=16, fontName='Helvetica-Bold',
                textColor=NAVY, leading=20, spaceBefore=14, spaceAfter=8)
    h2s    = ps('h2s',    fontSize=12, fontName='Helvetica-Bold',
                textColor=NAVY, leading=15, spaceBefore=10, spaceAfter=4)
    h3s    = ps('h3s',    fontSize=10.5, fontName='Helvetica-Bold',
                textColor=DGRAY, leading=13, spaceBefore=7, spaceAfter=3)
    tbl_h  = ps('tbl_h',  fontSize=8, fontName='Helvetica-Bold',
                textColor=colors.white, leading=10, alignment=TA_CENTER)
    tbl_c  = ps('tbl_c',  fontSize=8, leading=10, spaceAfter=0)
    bq     = ps('bq',     parent=body, leftIndent=16, rightIndent=8,
                fontName='Helvetica-Oblique', textColor=GRAY)

    # ── Markdown block → flowables ───────────────────────────────────────

    def rl_table(rows):
        if not rows:
            return []
        ncols = max(len(r) for r in rows)
        cw = (7.0 * inch) / ncols
        hdr = [Paragraph(md2rl(c), tbl_h) for c in rows[0]]
        data = [hdr]
        for row in rows[1:]:
            pad = (row + [''] * ncols)[:ncols]
            data.append([Paragraph(md2rl(c), tbl_c) for c in pad])
        t = Table(data, colWidths=[cw] * ncols, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0),  DGRAY),
            ('ROWBACKGROUNDS',(0, 1), (-1, -1), [colors.white, LGRAY]),
            ('GRID',          (0, 0), (-1, -1), 0.4, MGRAY),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ]))
        return [Spacer(1, 4), t, Spacer(1, 8)]

    def parse_tbl(tlines):
        rows = []
        for ln in tlines:
            if re.match(r'\|[-:\s|]+\|', ln.strip()):
                continue
            cells = [c.strip() for c in ln.strip().strip('|').split('|')]
            rows.append(cells)
        return rows

    def render_block(text: str) -> list:
        fl = []
        lines = text.split('\n')
        i, n = 0, len(lines)

        while i < n:
            ln = lines[i]
            s  = ln.strip()

            if not s:
                i += 1
                continue

            # Horizontal rule
            if re.match(r'^[-*]{3,}$', s):
                fl.append(HRFlowable(width='100%', thickness=0.4,
                                     color=MGRAY, spaceBefore=3, spaceAfter=3))
                i += 1
                continue

            # Headings  (longest prefix first)
            matched_heading = False
            for prefix, style in (('#### ', h3s), ('### ', h3s),
                                   ('## ', h2s), ('# ', h2s)):
                if s.startswith(prefix):
                    fl.append(Paragraph(md2rl(s[len(prefix):]), style))
                    i += 1
                    matched_heading = True
                    break
            if matched_heading:
                continue

            # Markdown table
            if (s.startswith('|') and i + 1 < n
                    and re.match(r'\|[-:\s|]+\|', lines[i + 1].strip())):
                tlines = []
                while i < n and lines[i].strip().startswith('|'):
                    tlines.append(lines[i])
                    i += 1
                fl.extend(rl_table(parse_tbl(tlines)))
                continue

            # Blockquote
            if s.startswith('>'):
                qlines = []
                while i < n and lines[i].strip().startswith('>'):
                    qlines.append(lines[i].strip().lstrip('> '))
                    i += 1
                fl.append(Paragraph(md2rl(' '.join(qlines)), bq))
                fl.append(Spacer(1, 3))
                continue

            # Bullet list
            if re.match(r'^\s*[-*]\s+', ln):
                while i < n and re.match(r'^\s*[-*]\s+', lines[i]):
                    m = re.match(r'^(\s*)[-*]\s+(.+)', lines[i])
                    if m:
                        st = nblt if len(m.group(1)) >= 2 else blt
                        fl.append(Paragraph(
                            f'&#x2022;&nbsp;{md2rl(m.group(2))}', st))
                    i += 1
                continue

            # Numbered list
            if re.match(r'^\s*\d+\.\s+', ln):
                while i < n and re.match(r'^\s*\d+\.\s+', lines[i]):
                    m = re.match(r'^\s*(\d+)\.\s+(.+)', lines[i])
                    if m:
                        fl.append(Paragraph(
                            f'<b>{m.group(1)}.</b>&nbsp;{md2rl(m.group(2))}',
                            blt))
                    i += 1
                continue

            # Regular paragraph — collect until blank / new block type
            plines = []
            while i < n:
                lx = lines[i]
                sx = lx.strip()
                if not sx:
                    break
                if (sx.startswith(('#', '|', '>'))
                        or re.match(r'^[-*]{3,}$', sx)
                        or re.match(r'^\s*[-*]\s+', lx)
                        or re.match(r'^\s*\d+\.\s+', lx)):
                    break
                plines.append(sx)
                i += 1
            if plines:
                pt = md2rl(' '.join(plines))
                if pt.strip():
                    fl.append(Paragraph(pt, body))
            if i < n and not lines[i].strip():
                i += 1

        return fl

    # ── Section / agent splitters ────────────────────────────────────────

    def split_sections(content):
        content = re.sub(r'^# Trading Analysis Report:.*', '', content,
                         flags=re.MULTILINE)
        content = re.sub(r'^Generated:.*', '', content, flags=re.MULTILINE)
        roman = r'(?:I{1,3}|I?V|VI{0,3})'
        parts = re.split(rf'^## ({roman})\. ', content, flags=re.MULTILINE)
        out, i = [], 1
        while i < len(parts) - 1:
            out.append((parts[i].strip(), parts[i + 1]))
            i += 2
        return out

    def split_agents(body):
        parts = re.split(r'^### (.+?)$', body, flags=re.MULTILINE)
        agents, i = [], 1
        while i < len(parts) - 1:
            agents.append((parts[i].strip(), parts[i + 1]))
            i += 2
        return agents

    # ── Agent section renderer ───────────────────────────────────────────

    _AGENT_PREFIX = re.compile(
        r'^(?:Bull|Bear|Market|Sentiment|News|Fundamentals|Research|Risk|'
        r'Aggressive|Conservative|Neutral|Portfolio)\s+'
        r'(?:Analyst|Researcher|Manager):\s*',
        re.IGNORECASE)

    def strip_prefix(text):
        return _AGENT_PREFIX.sub('', text.lstrip(), count=1)

    def render_agent(name: str, abody: str, final: bool = False) -> list:
        abody = strip_prefix(abody)
        bg_hex, fg_hex = agent_theme(name)

        hdr_st = ps('_ah', fontSize=11, fontName='Helvetica-Bold',
                    textColor=colors.HexColor(fg_hex), leading=14)
        hdr_tbl = Table([[Paragraph(name.upper(), hdr_st)]],
                        colWidths=[7.0 * inch])
        hdr_tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor(bg_hex)),
            ('TOPPADDING',    (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING',   (0, 0), (-1, -1), 12),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
        ]))

        fl = [Spacer(1, 10), hdr_tbl]

        # Portfolio Manager: extract key metrics into a highlight box
        if final:
            rm = re.search(r'\*\*Rating\*\*[:\s]+(\w+)', abody)
            tm = re.search(r'\*\*Price Target\*\*[:\s]+([\d.,]+)', abody)
            hm = re.search(r'\*\*Time Horizon\*\*[:\s]+([^\n]+)', abody)
            # fallback: plain "Rating: Overweight"
            if not rm:
                rm = re.search(r'Rating[:\s]+(\w+)', abody)

            parts = []
            if rm: parts.append(f'RATING: <b>{rm.group(1).upper()}</b>')
            if tm: parts.append(f'TARGET: <b>{tm.group(1)}</b>')
            if hm: parts.append(f'HORIZON: <b>{hm.group(1).strip()}</b>')

            if parts:
                rec_st = ps('_rec', fontSize=13, fontName='Helvetica-Bold',
                            textColor=colors.HexColor('#4D3900'),
                            leading=18, alignment=TA_CENTER)
                rec_tbl = Table(
                    [[Paragraph('   |   '.join(parts), rec_st)]],
                    colWidths=[7.0 * inch])
                rec_tbl.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF3CD')),
                    ('BOX',        (0, 0), (-1, -1), 2, GOLD),
                    ('TOPPADDING',    (0, 0), (-1, -1), 14),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
                    ('LEFTPADDING',   (0, 0), (-1, -1), 16),
                    ('RIGHTPADDING',  (0, 0), (-1, -1), 16),
                ]))
                fl += [Spacer(1, 10), rec_tbl, Spacer(1, 10)]

        fl += render_block(abody)
        fl.append(Spacer(1, 8))
        return fl

    # ── Story assembly ────────────────────────────────────────────────────

    SEC_LABELS = {
        'I':   'I.  Analyst Team Reports',
        'II':  'II.  Research Team Decision',
        'III': 'III.  Trading Team Plan',
        'IV':  'IV.  Risk Management Team',
        'V':   'V.  Portfolio Manager Decision',
    }

    def bar(colour, height=3):
        t = Table([['']], colWidths=[7 * inch], rowHeights=[height])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), colour),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return t

    story = []

    # Cover page
    story += [
        Spacer(1, 1.5 * inch),
        bar(NAVY, 3),
        Spacer(1, 0.25 * inch),
        Paragraph('INVESTMENT RESEARCH REPORT',
                  ps('_ct', fontSize=26, fontName='Helvetica-Bold',
                     textColor=NAVY, leading=32, alignment=TA_CENTER)),
        Spacer(1, 0.15 * inch),
        Paragraph(ticker,
                  ps('_ck', fontSize=40, fontName='Helvetica-Bold',
                     textColor=GOLD, leading=50, alignment=TA_CENTER)),
        Spacer(1, 0.1 * inch),
        bar(GOLD, 2),
        Spacer(1, 0.25 * inch),
        Paragraph(f'Analysis Date: {date}',
                  ps('_cd', fontSize=14, textColor=GRAY,
                     leading=18, alignment=TA_CENTER)),
        Spacer(1, 1.6 * inch),
        Paragraph('Prepared for: Kevin Cheng',
                  ps('_cp', fontSize=13, textColor=GRAY,
                     leading=16, alignment=TA_CENTER)),
        Spacer(1, 0.1 * inch),
        Paragraph('Powered by TradingAgents',
                  ps('_cq', fontSize=10, textColor=GRAY,
                     leading=13, alignment=TA_CENTER)),
        Paragraph('Multi-Agent LLM Financial Analysis',
                  ps('_cr', fontSize=10, textColor=MGRAY,
                     leading=13, alignment=TA_CENTER)),
        PageBreak(),
    ]

    # Content sections
    sections = split_sections(content)
    for idx, (sec_num, sec_body) in enumerate(sections):
        label    = SEC_LABELS.get(sec_num, sec_num)
        is_last  = (idx == len(sections) - 1)

        story.append(Paragraph(label, sec_h))
        story.append(HRFlowable(width='100%', thickness=1.5,
                                color=NAVY, spaceAfter=10))

        agents = split_agents(sec_body)
        if agents:
            for a_name, a_body in agents:
                is_pm = (sec_num == 'V') and 'Portfolio Manager' in a_name
                story += render_agent(a_name, a_body, final=is_pm)
        else:
            story += render_block(sec_body)

        if not is_last:
            story.append(PageBreak())

    # Build document
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch,  bottomMargin=0.9 * inch,
        title=f'Investment Research Report - {ticker}',
        author='TradingAgents',
    )
    doc.build(story,
              onFirstPage=lambda c, d: None,
              onLaterPages=draw_footer)

    shutil.copy2(pdf_path, latest)
    return pdf_path


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    base = Path(__file__).parent
    if len(sys.argv) > 1:
        rp = Path(sys.argv[1])
        if not rp.exists():
            sys.exit(f'ERROR: {rp} not found')
    else:
        rp = find_report(base)
    print(f'Source : {rp}')
    pdf = build_pdf(rp)
    print(f'PDF    : {pdf}')
    print(f'Latest : {base / "outputs" / "latest_report.pdf"}')
