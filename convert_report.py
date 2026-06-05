#!/usr/bin/env python3
"""
convert_report.py
Converts TradingAgents complete_report.md (or partial section files) to PDF.
Usage:
    python convert_report.py                     # auto-finds best report
    python convert_report.py path/to/report.md   # specific complete_report.md
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
    ('×','x'), ('÷','/'), ('≈','~'),
    ('≥','>='), ('≤','<='), ('°','deg'), ('±','+-'),
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


# ── Section name mapping (partial report support) ─────────────────────────────

# Maps filename stem → (section_title, agent_display_name)
_SECTION_MAP = {
    # Auto-save intermediate files (outputs/TICKER/DATE/reports/)
    'market_report':          ('I. Analyst Team Reports',      'Market Analyst'),
    'sentiment_report':       ('I. Analyst Team Reports',      'Sentiment Analyst'),
    'news_report':            ('I. Analyst Team Reports',      'News Analyst'),
    'fundamentals_report':    ('I. Analyst Team Reports',      'Fundamentals Analyst'),
    'investment_plan':        ('II. Research Team Decision',   'Research Manager'),
    'trader_investment_plan': ('III. Trading Team Plan',       'Trader'),
    'final_trade_decision':   ('V. Portfolio Manager Decision','Portfolio Manager'),
    # Manual save files (reports/TICKER_DATE/1_analysts/, 2_research/, etc.)
    'market':       ('I. Analyst Team Reports',      'Market Analyst'),
    'sentiment':    ('I. Analyst Team Reports',      'Sentiment Analyst'),
    'news':         ('I. Analyst Team Reports',      'News Analyst'),
    'fundamentals': ('I. Analyst Team Reports',      'Fundamentals Analyst'),
    'bull':         ('II. Research Team Decision',   'Bull Researcher'),
    'bear':         ('II. Research Team Decision',   'Bear Researcher'),
    'manager':      ('II. Research Team Decision',   'Research Manager'),
    'trader':       ('III. Trading Team Plan',       'Trader'),
    'aggressive':   ('IV. Risk Management Team',    'Aggressive Analyst'),
    'conservative': ('IV. Risk Management Team',    'Conservative Analyst'),
    'neutral':      ('IV. Risk Management Team',    'Neutral Analyst'),
    'decision':     ('V. Portfolio Manager Decision','Portfolio Manager'),
}

# Preferred display order
_SECTION_ORDER = [
    'market_report', 'market',
    'sentiment_report', 'sentiment',
    'news_report', 'news',
    'fundamentals_report', 'fundamentals',
    'investment_plan', 'bull', 'bear', 'manager',
    'trader_investment_plan', 'trader',
    'aggressive', 'conservative', 'neutral',
    'final_trade_decision', 'decision',
]


# ── File discovery ────────────────────────────────────────────────────────────

def find_complete_report(base: Path):
    """Return Path to the most recent complete_report.md, or None."""
    found = []
    for pat in [base / 'outputs' / '**' / 'complete_report.md',
                base / 'reports' / '**' / 'complete_report.md']:
        found += glob.glob(str(pat), recursive=True)
    if not found:
        return None
    return Path(max(found, key=os.path.getmtime))


def _extract_ticker_date(root: Path, base: Path):
    """Guess ticker and date from a report root directory."""
    ticker, date = 'UNKNOWN', datetime.now().strftime('%Y-%m-%d')
    try:
        parts = root.relative_to(base).parts
    except ValueError:
        parts = root.parts

    for part in parts:
        # Manual save: "0700.HK_20260605_105750"
        m = re.match(r'^(.+?)_(\d{4})(\d{2})(\d{2})_\d{6}$', part)
        if m:
            return m.group(1), f'{m.group(2)}-{m.group(3)}-{m.group(4)}'
        # Auto-save ticker dir like "0700.HK" or "NVDA"
        if (re.match(r'^[A-Z0-9]+(\.[A-Z]{2,})?$', part)
                and part not in ('outputs', 'reports')):
            ticker = part
        # Date dir like "2026-06-05"
        if re.match(r'^\d{4}-\d{2}-\d{2}$', part):
            date = part

    return ticker, date


def find_partial_sections(base: Path):
    """
    Find partial section files when no complete_report.md exists.
    Returns (ticker, date, [(section_title, agent_name, content)]) or (None, None, []).
    """
    # Locate report roots via anchor files
    auto_roots = set()
    for p in glob.glob(str(base / 'outputs' / '**' / 'message_tool.log'), recursive=True):
        auto_roots.add(Path(p).parent)

    manual_roots = set()
    reports_dir = base / 'reports'
    if reports_dir.exists():
        for d in reports_dir.iterdir():
            if d.is_dir() and re.match(r'^.+_\d{8}_\d{6}$', d.name):
                manual_roots.add(d)

    all_roots = list(auto_roots | manual_roots)
    if not all_roots:
        return None, None, []

    # Pick the most recently touched root
    def root_mtime(r):
        try:
            return max(f.stat().st_mtime for f in r.rglob('*.md'))
        except Exception:
            return 0.0

    best_root = max(all_roots, key=root_mtime)
    ticker, date = _extract_ticker_date(best_root, base)

    # Collect all .md files in this root (excluding complete_report.md)
    md_files = {f.stem: f for f in best_root.rglob('*.md')
                if f.name != 'complete_report.md'}

    # Build ordered section list
    sections = []
    seen_agents = set()
    for stem in _SECTION_ORDER:
        if stem not in md_files or stem not in _SECTION_MAP:
            continue
        sec_title, agent_name = _SECTION_MAP[stem]
        if agent_name in seen_agents:
            continue
        content = md_files[stem].read_text(encoding='utf-8', errors='replace')
        if content.strip():
            sections.append((sec_title, agent_name, clean(content)))
            seen_agents.add(agent_name)

    return (ticker, date, sections) if sections else (None, None, [])


# ── Shared rendering helpers (used by both PDF builders) ─────────────────────

def _check_reportlab():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        sys.exit('reportlab not installed. Run setup.bat or: pip install reportlab')


def _rl_imports():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable,
    )
    return (colors, letter, ParagraphStyle, inch,
            TA_CENTER, TA_LEFT, TA_JUSTIFY,
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, HRFlowable)


# ── Complete-report PDF builder ───────────────────────────────────────────────

def build_pdf(report_path: Path) -> Path:
    _check_reportlab()
    (colors, letter, ParagraphStyle, inch,
     TA_CENTER, TA_LEFT, TA_JUSTIFY,
     SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
     PageBreak, HRFlowable) = _rl_imports()

    raw = report_path.read_text(encoding='utf-8', errors='replace')
    content = clean(raw)

    m = re.search(r'#\s*Trading Analysis Report:\s*(.+)', content)
    ticker = m.group(1).strip() if m else 'UNKNOWN'
    m = re.search(r'Generated:\s*(\d{4}-\d{2}-\d{2})', content)
    date = m.group(1) if m else datetime.now().strftime('%Y-%m-%d')

    safe_ticker = re.sub(r'[\\/:*?"<>|]', '_', ticker)
    time_str = datetime.now().strftime('%H-%M-%S')
    filename = f'{safe_ticker}_{date}_{time_str}.pdf'

    script_dir = Path(__file__).parent
    # Primary: reports/TICKER/DATE/  — unique filename, never conflicts with open viewers
    primary_dir = script_dir / 'reports' / safe_ticker / date
    primary_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = primary_dir / filename
    # Latest folder: always has the most recent run, unique name so never locked
    latest_dir = script_dir / 'reports' / 'latest'
    latest_dir.mkdir(parents=True, exist_ok=True)

    NAVY  = colors.HexColor('#1B2B4B')
    GOLD  = colors.HexColor('#C09B3A')
    GRAY  = colors.HexColor('#6C757D')
    LGRAY = colors.HexColor('#F8F9FA')
    MGRAY = colors.HexColor('#CED4DA')
    DGRAY = colors.HexColor('#343A40')

    def draw_footer(canvas, doc):
        canvas.saveState()
        w, _ = letter
        canvas.setStrokeColor(MGRAY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75*inch, 0.65*inch, w - 0.75*inch, 0.65*inch)
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75*inch, 0.45*inch,
                          f'Investment Research Report  |  {ticker}')
        canvas.drawRightString(w - 0.75*inch, 0.45*inch,
                               f'{date}  |  Page {doc.page}')
        canvas.restoreState()

    def ps(name, **kw):
        from reportlab.lib.styles import getSampleStyleSheet
        parent = kw.pop('parent', getSampleStyleSheet()['Normal'])
        return ParagraphStyle(name, parent=parent, **kw)

    body   = ps('body',  fontSize=9.5, leading=14, spaceAfter=6,
                alignment=TA_JUSTIFY, textColor=colors.HexColor('#212529'))
    blt    = ps('blt',   parent=body, alignment=TA_LEFT,
                leftIndent=18, firstLineIndent=0, spaceAfter=3)
    nblt   = ps('nblt',  parent=blt, leftIndent=34)
    sec_h  = ps('sec_h', fontSize=16, fontName='Helvetica-Bold',
                textColor=NAVY, leading=20, spaceBefore=14, spaceAfter=8)
    h2s    = ps('h2s',   fontSize=12, fontName='Helvetica-Bold',
                textColor=NAVY, leading=15, spaceBefore=10, spaceAfter=4)
    h3s    = ps('h3s',   fontSize=10.5, fontName='Helvetica-Bold',
                textColor=DGRAY, leading=13, spaceBefore=7, spaceAfter=3)
    tbl_h  = ps('tbl_h', fontSize=8, fontName='Helvetica-Bold',
                textColor=colors.white, leading=10, alignment=TA_CENTER)
    tbl_c  = ps('tbl_c', fontSize=8, leading=10, spaceAfter=0)
    bq     = ps('bq',    parent=body, leftIndent=16, rightIndent=8,
                fontName='Helvetica-Oblique', textColor=GRAY)

    def rl_table(rows):
        if not rows:
            return []
        ncols = max(len(r) for r in rows)
        cw = (7.0 * inch) / ncols
        data = [[Paragraph(md2rl(c), tbl_h) for c in rows[0]]]
        for row in rows[1:]:
            pad = (row + [''] * ncols)[:ncols]
            data.append([Paragraph(md2rl(c), tbl_c) for c in pad])
        t = Table(data, colWidths=[cw] * ncols, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0),  DGRAY),
            ('ROWBACKGROUNDS',(0,1), (-1,-1), [colors.white, LGRAY]),
            ('GRID',          (0,0), (-1,-1), 0.4, MGRAY),
            ('TOPPADDING',    (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING',   (0,0), (-1,-1), 5),
            ('RIGHTPADDING',  (0,0), (-1,-1), 5),
            ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ]))
        return [Spacer(1,4), t, Spacer(1,8)]

    def parse_tbl(lines):
        rows = []
        for ln in lines:
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
                i += 1; continue
            if re.match(r'^[-*]{3,}$', s):
                fl.append(HRFlowable(width='100%', thickness=0.4,
                                     color=MGRAY, spaceBefore=3, spaceAfter=3))
                i += 1; continue
            matched = False
            for prefix, style in (('#### ',h3s),('### ',h3s),('## ',h2s),('# ',h2s)):
                if s.startswith(prefix):
                    fl.append(Paragraph(md2rl(s[len(prefix):]), style))
                    i += 1; matched = True; break
            if matched:
                continue
            if (s.startswith('|') and i+1 < n
                    and re.match(r'\|[-:\s|]+\|', lines[i+1].strip())):
                tlines = []
                while i < n and lines[i].strip().startswith('|'):
                    tlines.append(lines[i]); i += 1
                fl.extend(rl_table(parse_tbl(tlines))); continue
            if s.startswith('>'):
                qlines = []
                while i < n and lines[i].strip().startswith('>'):
                    qlines.append(lines[i].strip().lstrip('> ')); i += 1
                fl.append(Paragraph(md2rl(' '.join(qlines)), bq))
                fl.append(Spacer(1,3)); continue
            if re.match(r'^\s*[-*]\s+', ln):
                while i < n and re.match(r'^\s*[-*]\s+', lines[i]):
                    m2 = re.match(r'^(\s*)[-*]\s+(.+)', lines[i])
                    if m2:
                        st = nblt if len(m2.group(1)) >= 2 else blt
                        fl.append(Paragraph(
                            f'&#x2022;&nbsp;{md2rl(m2.group(2))}', st))
                    i += 1
                continue
            if re.match(r'^\s*\d+\.\s+', ln):
                while i < n and re.match(r'^\s*\d+\.\s+', lines[i]):
                    m2 = re.match(r'^\s*(\d+)\.\s+(.+)', lines[i])
                    if m2:
                        fl.append(Paragraph(
                            f'<b>{m2.group(1)}.</b>&nbsp;{md2rl(m2.group(2))}', blt))
                    i += 1
                continue
            plines = []
            while i < n:
                lx = lines[i]; sx = lx.strip()
                if not sx: break
                if (sx.startswith(('#','|','>'))
                        or re.match(r'^[-*]{3,}$', sx)
                        or re.match(r'^\s*[-*]\s+', lx)
                        or re.match(r'^\s*\d+\.\s+', lx)):
                    break
                plines.append(sx); i += 1
            if plines:
                pt = md2rl(' '.join(plines))
                if pt.strip():
                    fl.append(Paragraph(pt, body))
            if i < n and not lines[i].strip():
                i += 1
        return fl

    _AGENT_PREFIX = re.compile(
        r'^(?:Bull|Bear|Market|Sentiment|News|Fundamentals|Research|Risk|'
        r'Aggressive|Conservative|Neutral|Portfolio)\s+'
        r'(?:Analyst|Researcher|Manager):\s*', re.IGNORECASE)

    def render_agent(name: str, abody: str, final: bool = False) -> list:
        abody = _AGENT_PREFIX.sub('', abody.lstrip(), count=1)
        bg_hex, fg_hex = agent_theme(name)
        hdr_st = ps('_ah', fontSize=11, fontName='Helvetica-Bold',
                    textColor=colors.HexColor(fg_hex), leading=14)
        hdr_tbl = Table([[Paragraph(name.upper(), hdr_st)]],
                        colWidths=[7.0*inch])
        hdr_tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,-1), colors.HexColor(bg_hex)),
            ('TOPPADDING',    (0,0),(-1,-1), 8),
            ('BOTTOMPADDING', (0,0),(-1,-1), 8),
            ('LEFTPADDING',   (0,0),(-1,-1), 12),
            ('RIGHTPADDING',  (0,0),(-1,-1), 12),
        ]))
        fl = [Spacer(1,10), hdr_tbl]
        if final:
            rm = re.search(r'\*\*Rating\*\*[:\s]+(\w+)', abody) or \
                 re.search(r'Rating[:\s]+(\w+)', abody)
            tm = re.search(r'\*\*Price Target\*\*[:\s]+([\d.,]+)', abody)
            hm = re.search(r'\*\*Time Horizon\*\*[:\s]+([^\n]+)', abody)
            parts = []
            if rm: parts.append(f'RATING: <b>{rm.group(1).upper()}</b>')
            if tm: parts.append(f'TARGET: <b>{tm.group(1)}</b>')
            if hm: parts.append(f'HORIZON: <b>{hm.group(1).strip()}</b>')
            if parts:
                rec_st = ps('_rec', fontSize=13, fontName='Helvetica-Bold',
                            textColor=colors.HexColor('#4D3900'),
                            leading=18, alignment=TA_CENTER)
                rec_tbl = Table([[Paragraph('   |   '.join(parts), rec_st)]],
                                colWidths=[7.0*inch])
                rec_tbl.setStyle(TableStyle([
                    ('BACKGROUND',(0,0),(-1,-1), colors.HexColor('#FFF3CD')),
                    ('BOX',       (0,0),(-1,-1), 2, GOLD),
                    ('TOPPADDING',    (0,0),(-1,-1), 14),
                    ('BOTTOMPADDING', (0,0),(-1,-1), 14),
                    ('LEFTPADDING',   (0,0),(-1,-1), 16),
                    ('RIGHTPADDING',  (0,0),(-1,-1), 16),
                ]))
                fl += [Spacer(1,10), rec_tbl, Spacer(1,10)]
        fl += render_block(abody)
        fl.append(Spacer(1,8))
        return fl

    def bar(colour, height=3):
        t = Table([['']], colWidths=[7*inch], rowHeights=[height])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,-1), colour),
            ('TOPPADDING',    (0,0),(-1,-1), 0),
            ('BOTTOMPADDING', (0,0),(-1,-1), 0),
        ]))
        return t

    def split_sections(content):
        content = re.sub(r'^# Trading Analysis Report:.*', '', content, flags=re.MULTILINE)
        content = re.sub(r'^Generated:.*', '', content, flags=re.MULTILINE)
        roman = r'(?:I{1,3}|I?V|VI{0,3})'
        parts = re.split(rf'^## ({roman})\. ', content, flags=re.MULTILINE)
        out, i = [], 1
        while i < len(parts) - 1:
            out.append((parts[i].strip(), parts[i+1]))
            i += 2
        return out

    def split_agents(body):
        parts = re.split(r'^### (.+?)$', body, flags=re.MULTILINE)
        agents, i = [], 1
        while i < len(parts) - 1:
            agents.append((parts[i].strip(), parts[i+1]))
            i += 2
        return agents

    SEC_LABELS = {
        'I':  'I.  Analyst Team Reports',
        'II': 'II.  Research Team Decision',
        'III':'III.  Trading Team Plan',
        'IV': 'IV.  Risk Management Team',
        'V':  'V.  Portfolio Manager Decision',
    }

    story = []
    story += [
        Spacer(1, 1.5*inch), bar(NAVY, 3), Spacer(1, 0.25*inch),
        Paragraph('INVESTMENT RESEARCH REPORT',
                  ps('_ct', fontSize=26, fontName='Helvetica-Bold',
                     textColor=NAVY, leading=32, alignment=TA_CENTER)),
        Spacer(1, 0.15*inch),
        Paragraph(ticker,
                  ps('_ck', fontSize=40, fontName='Helvetica-Bold',
                     textColor=GOLD, leading=50, alignment=TA_CENTER)),
        Spacer(1, 0.1*inch), bar(GOLD, 2), Spacer(1, 0.25*inch),
        Paragraph(f'Analysis Date: {date}',
                  ps('_cd', fontSize=14, textColor=GRAY,
                     leading=18, alignment=TA_CENTER)),
        Spacer(1, 1.6*inch),
        Paragraph('Prepared for: Kevin Cheng',
                  ps('_cp', fontSize=13, textColor=GRAY,
                     leading=16, alignment=TA_CENTER)),
        Spacer(1, 0.1*inch),
        Paragraph('Powered by TradingAgents',
                  ps('_cq', fontSize=10, textColor=GRAY,
                     leading=13, alignment=TA_CENTER)),
        Paragraph('Multi-Agent LLM Financial Analysis',
                  ps('_cr', fontSize=10, textColor=MGRAY,
                     leading=13, alignment=TA_CENTER)),
        PageBreak(),
    ]

    sections = split_sections(content)
    for idx, (sec_num, sec_body) in enumerate(sections):
        label   = SEC_LABELS.get(sec_num, sec_num)
        is_last = (idx == len(sections) - 1)
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

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.9*inch,
        title=f'Investment Research Report - {ticker}',
        author='TradingAgents',
    )
    doc.build(story,
              onFirstPage=lambda c, d: None,
              onLaterPages=draw_footer)

    # Unique filename → no viewer can have this exact file open yet
    latest_path = latest_dir / filename
    shutil.copy2(pdf_path, latest_path)

    return pdf_path, latest_path


# ── Partial-report PDF builder ────────────────────────────────────────────────

def build_partial_pdf(sections: list, ticker: str, date: str, base: Path) -> Path:
    """
    Build a PDF from partial section files.
    Cover is clearly marked INCOMPLETE ANALYSIS.
    """
    _check_reportlab()
    (colors, letter, ParagraphStyle, inch,
     TA_CENTER, TA_LEFT, TA_JUSTIFY,
     SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
     PageBreak, HRFlowable) = _rl_imports()

    safe_ticker = re.sub(r'[\\/:*?"<>|]', '_', ticker)
    time_str = datetime.now().strftime('%H-%M-%S')
    filename = f'partial_{safe_ticker}_{date}_{time_str}.pdf'

    script_dir = Path(__file__).parent
    primary_dir = script_dir / 'reports' / safe_ticker / date
    primary_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = primary_dir / filename
    latest_dir = script_dir / 'reports' / 'latest'
    latest_dir.mkdir(parents=True, exist_ok=True)

    AMBER  = colors.HexColor('#C09B3A')
    ORANGE = colors.HexColor('#8B4000')
    WARN   = colors.HexColor('#856404')
    WARN_BG= colors.HexColor('#FFF3CD')
    GRAY   = colors.HexColor('#6C757D')
    LGRAY  = colors.HexColor('#F8F9FA')
    MGRAY  = colors.HexColor('#CED4DA')
    DGRAY  = colors.HexColor('#343A40')
    RED    = colors.HexColor('#721C24')
    RED_BG = colors.HexColor('#F8D7DA')

    def draw_footer(canvas, doc):
        canvas.saveState()
        w, _ = letter
        canvas.setStrokeColor(MGRAY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75*inch, 0.65*inch, w - 0.75*inch, 0.65*inch)
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75*inch, 0.45*inch,
                          f'INCOMPLETE — {ticker}  |  API credit may have been exhausted')
        canvas.drawRightString(w - 0.75*inch, 0.45*inch,
                               f'{date}  |  Page {doc.page}')
        canvas.restoreState()

    def ps(name, **kw):
        from reportlab.lib.styles import getSampleStyleSheet
        parent = kw.pop('parent', getSampleStyleSheet()['Normal'])
        return ParagraphStyle(name, parent=parent, **kw)

    body  = ps('pbody', fontSize=9.5, leading=14, spaceAfter=6,
               alignment=TA_JUSTIFY, textColor=colors.HexColor('#212529'))
    blt   = ps('pblt',  parent=body, alignment=TA_LEFT,
               leftIndent=18, firstLineIndent=0, spaceAfter=3)
    nblt  = ps('pnblt', parent=blt,  leftIndent=34)
    h2s   = ps('ph2s',  fontSize=12, fontName='Helvetica-Bold',
               textColor=ORANGE, leading=15, spaceBefore=10, spaceAfter=4)
    h3s   = ps('ph3s',  fontSize=10.5, fontName='Helvetica-Bold',
               textColor=DGRAY, leading=13, spaceBefore=7, spaceAfter=3)
    tbl_h = ps('ptbl_h',fontSize=8, fontName='Helvetica-Bold',
               textColor=colors.white, leading=10, alignment=TA_CENTER)
    tbl_c = ps('ptbl_c',fontSize=8, leading=10, spaceAfter=0)
    bq    = ps('pbq',   parent=body, leftIndent=16, fontName='Helvetica-Oblique',
               textColor=GRAY)

    def rl_table(rows):
        if not rows:
            return []
        ncols = max(len(r) for r in rows)
        cw = (7.0 * inch) / ncols
        data = [[Paragraph(md2rl(c), tbl_h) for c in rows[0]]]
        for row in rows[1:]:
            pad = (row + [''] * ncols)[:ncols]
            data.append([Paragraph(md2rl(c), tbl_c) for c in pad])
        t = Table(data, colWidths=[cw]*ncols, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,0),  DGRAY),
            ('ROWBACKGROUNDS',(0,1),(-1,-1), [colors.white, LGRAY]),
            ('GRID',          (0,0),(-1,-1), 0.4, MGRAY),
            ('TOPPADDING',    (0,0),(-1,-1), 4),
            ('BOTTOMPADDING', (0,0),(-1,-1), 4),
            ('LEFTPADDING',   (0,0),(-1,-1), 5),
            ('RIGHTPADDING',  (0,0),(-1,-1), 5),
            ('VALIGN',        (0,0),(-1,-1), 'TOP'),
        ]))
        return [Spacer(1,4), t, Spacer(1,8)]

    def parse_tbl(lines):
        rows = []
        for ln in lines:
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
            ln = lines[i]; s = ln.strip()
            if not s: i += 1; continue
            if re.match(r'^[-*]{3,}$', s):
                fl.append(HRFlowable(width='100%', thickness=0.4,
                                     color=MGRAY, spaceBefore=3, spaceAfter=3))
                i += 1; continue
            matched = False
            for prefix, sty in (('#### ',h3s),('### ',h3s),('## ',h2s),('# ',h2s)):
                if s.startswith(prefix):
                    fl.append(Paragraph(md2rl(s[len(prefix):]), sty))
                    i += 1; matched = True; break
            if matched: continue
            if (s.startswith('|') and i+1 < n
                    and re.match(r'\|[-:\s|]+\|', lines[i+1].strip())):
                tlines = []
                while i < n and lines[i].strip().startswith('|'):
                    tlines.append(lines[i]); i += 1
                fl.extend(rl_table(parse_tbl(tlines))); continue
            if s.startswith('>'):
                qlines = []
                while i < n and lines[i].strip().startswith('>'):
                    qlines.append(lines[i].strip().lstrip('> ')); i += 1
                fl.append(Paragraph(md2rl(' '.join(qlines)), bq))
                fl.append(Spacer(1,3)); continue
            if re.match(r'^\s*[-*]\s+', ln):
                while i < n and re.match(r'^\s*[-*]\s+', lines[i]):
                    m2 = re.match(r'^(\s*)[-*]\s+(.+)', lines[i])
                    if m2:
                        st = nblt if len(m2.group(1)) >= 2 else blt
                        fl.append(Paragraph(f'&#x2022;&nbsp;{md2rl(m2.group(2))}', st))
                    i += 1
                continue
            if re.match(r'^\s*\d+\.\s+', ln):
                while i < n and re.match(r'^\s*\d+\.\s+', lines[i]):
                    m2 = re.match(r'^\s*(\d+)\.\s+(.+)', lines[i])
                    if m2:
                        fl.append(Paragraph(
                            f'<b>{m2.group(1)}.</b>&nbsp;{md2rl(m2.group(2))}', blt))
                    i += 1
                continue
            plines = []
            while i < n:
                lx = lines[i]; sx = lx.strip()
                if not sx: break
                if (sx.startswith(('#','|','>')) or re.match(r'^[-*]{3,}$',sx)
                        or re.match(r'^\s*[-*]\s+',lx) or re.match(r'^\s*\d+\.\s+',lx)):
                    break
                plines.append(sx); i += 1
            if plines:
                pt = md2rl(' '.join(plines))
                if pt.strip():
                    fl.append(Paragraph(pt, body))
            if i < n and not lines[i].strip(): i += 1
        return fl

    _AGENT_PREFIX = re.compile(
        r'^(?:Bull|Bear|Market|Sentiment|News|Fundamentals|Research|Risk|'
        r'Aggressive|Conservative|Neutral|Portfolio)\s+'
        r'(?:Analyst|Researcher|Manager):\s*', re.IGNORECASE)

    def render_agent(name, abody):
        abody = _AGENT_PREFIX.sub('', abody.lstrip(), count=1)
        bg_hex, fg_hex = agent_theme(name)
        hdr_st = ps('_pah', fontSize=11, fontName='Helvetica-Bold',
                    textColor=colors.HexColor(fg_hex), leading=14)
        hdr_tbl = Table([[Paragraph(name.upper(), hdr_st)]],
                        colWidths=[7.0*inch])
        hdr_tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,-1), colors.HexColor(bg_hex)),
            ('TOPPADDING',    (0,0),(-1,-1), 8),
            ('BOTTOMPADDING', (0,0),(-1,-1), 8),
            ('LEFTPADDING',   (0,0),(-1,-1), 12),
            ('RIGHTPADDING',  (0,0),(-1,-1), 12),
        ]))
        return [Spacer(1,10), hdr_tbl] + render_block(abody) + [Spacer(1,8)]

    def bar(colour, height=3):
        t = Table([['']], colWidths=[7*inch], rowHeights=[height])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,-1), colour),
            ('TOPPADDING',    (0,0),(-1,-1), 0),
            ('BOTTOMPADDING', (0,0),(-1,-1), 0),
        ]))
        return t

    completed = [a for _, a, _ in sections]

    story = [
        Spacer(1, 1.0*inch), bar(ORANGE, 4), Spacer(1, 0.2*inch),
        Paragraph('INCOMPLETE INVESTMENT RESEARCH REPORT',
                  ps('_pct', fontSize=22, fontName='Helvetica-Bold',
                     textColor=ORANGE, leading=28, alignment=TA_CENTER)),
        Spacer(1, 0.1*inch),
        Paragraph(ticker,
                  ps('_pck', fontSize=36, fontName='Helvetica-Bold',
                     textColor=AMBER, leading=44, alignment=TA_CENTER)),
        Spacer(1, 0.08*inch), bar(AMBER, 2), Spacer(1, 0.2*inch),
        Paragraph(f'Analysis Date: {date}',
                  ps('_pcd', fontSize=13, textColor=GRAY,
                     leading=16, alignment=TA_CENTER)),
        Spacer(1, 0.3*inch),
    ]

    # Warning box on cover
    warn_st = ps('_pw', fontSize=10, fontName='Helvetica-Bold',
                 textColor=WARN, leading=14, alignment=TA_CENTER)
    warn_body = ps('_pwb', fontSize=9, textColor=RED,
                   leading=13, alignment=TA_CENTER)
    warn_tbl = Table([
        [Paragraph('[!]  ANALYSIS INCOMPLETE — POSSIBLE API CREDIT EXHAUSTION', warn_st)],
        [Paragraph(
            'The analysis was interrupted before completion. '
            'Check platform.anthropic.com for API credit balance. '
            'Completed sections are shown below.',
            warn_body)],
    ], colWidths=[7.0*inch])
    warn_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(-1,-1), WARN_BG),
        ('BOX',           (0,0),(-1,-1), 2, WARN),
        ('TOPPADDING',    (0,0),(-1,-1), 10),
        ('BOTTOMPADDING', (0,0),(-1,-1), 10),
        ('LEFTPADDING',   (0,0),(-1,-1), 14),
        ('RIGHTPADDING',  (0,0),(-1,-1), 14),
    ]))
    story += [warn_tbl, Spacer(1, 0.25*inch)]

    # Completed sections list on cover
    comp_st = ps('_pcs', fontSize=11, textColor=GRAY, leading=16, alignment=TA_CENTER)
    story.append(Paragraph(
        f'Completed sections ({len(sections)}): '
        + ', '.join(completed),
        comp_st))
    story += [
        Spacer(1, 0.6*inch),
        Paragraph('Prepared for: Kevin Cheng',
                  ps('_pcp', fontSize=12, textColor=GRAY,
                     leading=15, alignment=TA_CENTER)),
        Spacer(1, 0.08*inch),
        Paragraph('Powered by TradingAgents',
                  ps('_pcq', fontSize=10, textColor=GRAY,
                     leading=13, alignment=TA_CENTER)),
        PageBreak(),
    ]

    # Content
    prev_sec = None
    for sec_title, agent_name, agent_content in sections:
        if sec_title != prev_sec:
            if prev_sec is not None:
                story.append(PageBreak())
            sec_h = ps(f'_psh_{sec_title[:4]}', fontSize=15,
                       fontName='Helvetica-Bold', textColor=ORANGE,
                       leading=19, spaceBefore=12, spaceAfter=8)
            story.append(Paragraph(sec_title, sec_h))
            story.append(HRFlowable(width='100%', thickness=1.5,
                                    color=AMBER, spaceAfter=10))
            prev_sec = sec_title
        story += render_agent(agent_name, agent_content)

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.9*inch,
        title=f'Partial Investment Research Report - {ticker}',
        author='TradingAgents',
    )
    doc.build(story,
              onFirstPage=lambda c, d: None,
              onLaterPages=draw_footer)

    latest_path = latest_dir / filename
    shutil.copy2(pdf_path, latest_path)

    return pdf_path, latest_path


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    base = Path(__file__).parent

    # Specific file supplied
    if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        rp = Path(sys.argv[1])
        if not rp.exists():
            sys.exit(f'ERROR: {rp} not found')
        print(f'Source  : {rp}')
        primary, latest = build_pdf(rp)
        print(f'PDF     : {primary}')
        print(f'Latest  : {latest}')
        sys.exit(0)

    # Auto-find: use whichever is MORE RECENT — complete report or partial sections.
    # This prevents an old complete_report.md (e.g. MU) from shadowing fresh
    # partial sections from a newer crashed run (e.g. GOOGL).
    rp = find_complete_report(base)
    ticker, date, sections = find_partial_sections(base)

    # If both exist, keep whichever was written most recently
    if rp and sections:
        partial_files = (
            glob.glob(str(base / 'outputs' / '**' / '*.md'), recursive=True) +
            glob.glob(str(base / 'reports' / '**' / '*.md'), recursive=True)
        )
        partial_files = [f for f in partial_files if 'complete_report' not in f]
        if partial_files:
            latest_partial_mtime = max(os.path.getmtime(f) for f in partial_files)
            if latest_partial_mtime > os.path.getmtime(rp):
                rp = None   # partial sections are newer — use them instead

    if rp is not None:
        print(f'Source  : {rp}')
        primary, latest = build_pdf(rp)
        print(f'PDF     : {primary}')
        print(f'Latest  : {latest}')
        sys.exit(0)

    if sections:
        print(f'No complete_report.md found — building partial PDF from '
              f'{len(sections)} section(s)...')
        primary, latest = build_partial_pdf(sections, ticker, date, base)
        print(f'PDF     : {primary}')
        print(f'Latest  : {latest}')
        sys.exit(0)

    sys.exit(
        'ERROR: No report files found in outputs/ or reports/\n'
        'Run TradingAgents and press Y at the "Save report?" prompt.\n'
        'If Anthropic credit ran out, check platform.anthropic.com/billing')
