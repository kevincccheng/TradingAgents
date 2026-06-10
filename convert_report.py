#!/usr/bin/env python3
"""
convert_report.py
Converts TradingAgents complete_report.md (or partial section files) to PDF.
Usage:
    python convert_report.py                     # auto-finds best report
    python convert_report.py path/to/report.md   # specific complete_report.md
"""
import os, sys, re, glob, shutil, subprocess
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


# Verdict colour table: BUY = green, HOLD = amber, SELL = red
# (header text colour, light background colour)
_VERDICT_THEME = {
    'BUY':  ('#1E7B34', '#D4EDDA'),
    'HOLD': ('#946C00', '#FFF3CD'),
    'SELL': ('#A12D2D', '#F8D7DA'),
}


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


# ── Distilled-report file discovery ───────────────────────────────────────────

def find_report_root(base: Path):
    """
    Find the most recently-touched report root directory, regardless of
    whether the run is complete. Returns (root, ticker, date) or
    (None, None, None).
    """
    candidates = set()

    reports_dir = base / 'reports'
    if reports_dir.exists():
        for d in reports_dir.iterdir():
            if d.is_dir() and re.match(r'^.+_\d{8}_\d{6}$', d.name):
                candidates.add(d)

    for p in glob.glob(str(base / 'outputs' / '**' / 'reports'), recursive=True):
        d = Path(p).parent
        if d.is_dir():
            candidates.add(d)

    if not candidates:
        return None, None, None

    def root_mtime(r):
        try:
            return max(f.stat().st_mtime for f in r.rglob('*.md'))
        except Exception:
            return 0.0

    best = max(candidates, key=root_mtime)
    if root_mtime(best) == 0.0:
        return None, None, None

    ticker, date = _extract_ticker_date(best, base)
    return best, ticker, date


# Maps canonical section key -> candidate filenames (manual-save, then auto-save)
_DISTILL_FILE_MAP = {
    'market':       ['market.md', 'market_report.md'],
    'fundamentals': ['fundamentals.md', 'fundamentals_report.md'],
    'news':         ['news.md', 'news_report.md'],
    'sentiment':    ['sentiment.md', 'sentiment_report.md'],
    'bull':         ['bull.md'],
    'bear':         ['bear.md'],
    'manager':      ['manager.md', 'investment_plan.md'],
    'trader':       ['trader.md', 'trader_investment_plan.md'],
    'aggressive':   ['aggressive.md'],
    'conservative': ['conservative.md'],
    'neutral':      ['neutral.md'],
    'decision':     ['decision.md', 'final_trade_decision.md'],
}


def gather_distilled_sections(root: Path) -> dict:
    """Return {canonical_key: cleaned_text} for every recognised section file
    found under root (empty string if missing)."""
    all_files = {}
    for f in root.rglob('*.md'):
        all_files.setdefault(f.name, f)

    out = {}
    for key, names in _DISTILL_FILE_MAP.items():
        content = ''
        for nm in names:
            if nm in all_files:
                content = clean(all_files[nm].read_text(encoding='utf-8', errors='replace'))
                break
        out[key] = content
    return out


def archive_full_debate(root: Path, ticker: str, date: str, dest_dir: Path) -> Path:
    """Write the full (un-distilled) debate transcript next to the PDF."""
    safe_ticker = re.sub(r'[\\/:*?"<>|]', '_', ticker)
    archive_path = dest_dir / f'{safe_ticker}_{date}_full_debate.md'

    complete = root / 'complete_report.md'
    if complete.exists():
        text = complete.read_text(encoding='utf-8', errors='replace')
    else:
        parts = [f'# Full Debate Archive: {ticker}\n\nGenerated: {date}\n']
        for f in sorted(root.rglob('*.md')):
            rel = f.relative_to(root)
            parts.append(f'\n\n## {rel}\n\n')
            parts.append(f.read_text(encoding='utf-8', errors='replace'))
        text = ''.join(parts)

    archive_path.write_text(text, encoding='utf-8')
    return archive_path


def open_in_edge(pdf_path: Path) -> bool:
    """Best-effort auto-open of the generated PDF in Microsoft Edge."""
    edge_paths = [
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    ]
    for ep in edge_paths:
        if Path(ep).exists():
            try:
                subprocess.Popen([ep, str(pdf_path)])
                return True
            except Exception:
                pass
    try:
        os.startfile(str(pdf_path))
        return True
    except Exception:
        return False


# ── Distillation helpers ───────────────────────────────────────────────────────

def parse_tbl(lines):
    rows = []
    for ln in lines:
        if re.match(r'\|[-:\s|]+\|', ln.strip()):
            continue
        cells = [c.strip() for c in ln.strip().strip('|').split('|')]
        rows.append(cells)
    return rows


_ROUND_RE = re.compile(
    r'^(?:Bull|Bear|Conservative|Aggressive|Neutral) Analyst:\s*', re.MULTILINE)


def split_rounds(text):
    """Split a debate transcript into (round_text) chunks, one per round."""
    parts = _ROUND_RE.split(text)
    return [p for p in parts if p.strip()]


def first_round(text):
    rounds = split_rounds(text)
    return rounds[0] if rounds else text


_QUOTE_RE_CACHE = {}


def agent_quotes(round_text, label):
    """Pull out only this agent's own quoted lines from an interleaved
    bull/bear transcript. Falls back to the whole round if there are no
    embedded quote markers (aggressive/conservative/neutral monologues)."""
    pat = _QUOTE_RE_CACHE.get(label)
    if pat is None:
        pat = re.compile(
            rf'\*\*{label} Analyst:\*\*\s*(.*?)(?=\n\n\*\*\w+ Analyst:\*\*|\Z)',
            re.DOTALL)
        _QUOTE_RE_CACHE[label] = pat
    matches = pat.findall(round_text)
    if matches:
        return ' '.join(m.strip() for m in matches)
    return round_text.strip()


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9$"‘’“”])')
_LEAD_BOLD = re.compile(r'^\*\*[^*]{1,60}\*\*:?\s*')
_HEADING_LINE = re.compile(r'^#{1,4}\s*.*$', re.MULTILINE)
_HR_LINE = re.compile(r'^\s*([-*_])(?:\s*\1){2,}\s*$', re.MULTILINE)


def extract_sentences(text):
    """Collapse a markdown blob to plain text and split into sentences."""
    text = _HEADING_LINE.sub('', text or '')
    text = _HR_LINE.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = _LEAD_BOLD.sub('', text)
    text = text.strip(' "“”')
    if not text:
        return []
    sents = _SENT_SPLIT.split(text)
    out = []
    for s in sents:
        s = s.strip().strip('"“”')
        if s:
            out.append(s)
    return out


def _word_set(s):
    return set(re.findall(r'[a-z0-9]+', s.lower()))


def dedupe_sentences(sentences, max_n=6, min_words=4, prefer_numeric=False):
    """Keep the first occurrence of each roughly-distinct claim.

    With prefer_numeric=True, sentences containing a number/%/$ (the
    factual data points the distillation rules say to keep) are
    considered before purely qualitative ones.
    """
    candidates = [s for s in sentences if len(_word_set(s)) >= min_words]
    if prefer_numeric:
        numeric = [s for s in candidates if re.search(r'[\d$%]', s)]
        other = [s for s in candidates if not re.search(r'[\d$%]', s)]
        candidates = numeric + other

    out, seen = [], []
    for s in candidates:
        ws = _word_set(s)
        dup = False
        for prev in seen:
            union = ws | prev
            if union and len(ws & prev) / len(union) > 0.5:
                dup = True
                break
        if not dup:
            out.append(s)
            seen.append(ws)
        if len(out) >= max_n:
            break
    return out


_REC_PATTERNS = [
    re.compile(r'FINAL TRANSACTION PROPOSAL:\s*\*{0,2}([A-Za-z]+)\*{0,2}', re.IGNORECASE),
    re.compile(r'Final Recommendation:\s*\*{0,2}([A-Za-z]+)\*{0,2}', re.IGNORECASE),
    re.compile(r'\*\*Recommendation\*\*:\s*([A-Za-z]+)', re.IGNORECASE),
    re.compile(r'\*\*Final Trading Decision:\s*([A-Za-z]+)\*\*', re.IGNORECASE),
    re.compile(r'\*\*Rating:\s*([A-Za-z]+)\*\*', re.IGNORECASE),
    re.compile(r"My (?:final )?(?:recommendation|transaction proposal)[^.]*?is\s*"
               r"(?:a\s*)?\*{0,2}([A-Za-z]+)\*{0,2}", re.IGNORECASE),
]


def extract_recommendation(text):
    """Return the last BUY/SELL/HOLD call found in text, or None."""
    found = None
    for pat in _REC_PATTERNS:
        for m in pat.finditer(text):
            found = m.group(1).upper()
    if found:
        for word in ('BUY', 'SELL', 'HOLD'):
            if word in found:
                return word
    return None


def extract_company_name(fundamentals_text):
    if not fundamentals_text:
        return None
    m = re.search(r'Fundamental Analysis Report:\s*[\w.]+\s*\(([^)]+)\)', fundamentals_text)
    if m:
        return m.group(1).strip()
    m = re.search(r'\|\s*Company\s*Name\s*\|\s*([^|\n]+)\|', fundamentals_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Fallback: a heading line of the form "## Company Name, Inc. (TICKER)"
    m = re.search(r'^#{1,3}\s*([^(\n]+?)\s*\([A-Z][A-Z0-9.]*\)\s*$', fundamentals_text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None


def extract_data_sources_header(text):
    """Pull the FIX1 'DATA SOURCES USED FOR THIS ANALYSIS' block, if present."""
    if not text:
        return None
    m = re.search(r'DATA SOURCES USED FOR THIS ANALYSIS\s*\n((?:.+\n?){1,8})', text)
    if not m:
        return None
    lines = []
    for l in m.group(1).split('\n'):
        l = l.strip(' -_─')
        if not l:
            break
        lines.append(l)
    return lines or None


# ── Per-section extractors ──────────────────────────────────────────────────────

def extract_verdict(text):
    """Portfolio Manager decision -> recommendation / rationale / actions."""
    rec = (re.search(r'\*\*Recommendation\*\*:\s*([A-Za-z]+)', text)
           or re.search(r'\*\*Final Trading Decision:\s*([A-Za-z]+)\*\*', text)
           or re.search(r'\*\*Rating:\s*([A-Za-z]+)\*\*', text))
    recommendation = (rec.group(1).upper() if rec else
                       (extract_recommendation(text) or 'HOLD'))
    for word in ('BUY', 'SELL', 'HOLD'):
        if word in recommendation:
            recommendation = word
            break
    else:
        recommendation = 'HOLD'

    rat_m = re.search(
        r'\*\*Rationale\*\*:?\s*(.*?)(?=\n\n\*{0,2}\d+\.|\n\n\*\*Strategic|\Z)',
        text, re.DOTALL)
    rationale = extract_sentences(rat_m.group(1))[:3] if rat_m else []

    actions = []
    sa_m = re.search(
        r'\*\*Strategic Actions\*\*:?\s*(.*?)(?=\n\n\*\*[A-Z][\w ]*\*\*\s*\n|\Z)',
        text, re.DOTALL)
    if sa_m:
        for m in re.finditer(r'\d+\.\s*(.+?)(?=\n\d+\.|\Z)', sa_m.group(1), re.DOTALL):
            a = re.sub(r'\s+', ' ', m.group(1)).strip()
            if a:
                actions.append(a)

    return {'recommendation': recommendation, 'rationale': rationale, 'actions': actions[:5]}


def extract_risk_view(text, max_n=4):
    """Conservative / Aggressive / Neutral risk-team round-1 view -> bullets."""
    if not text:
        return []
    sentences = extract_sentences(first_round(text))
    return dedupe_sentences(sentences, max_n, min_words=7, prefer_numeric=True)


def extract_research_view(text, label):
    """Bull / Bear researcher round-1 -> thesis sentences + supporting args."""
    round1 = first_round(text)
    quotes = agent_quotes(round1, label)
    sentences = extract_sentences(quotes)
    substantive = [s for s in sentences if len(_word_set(s)) >= 7]
    pool = substantive if substantive else sentences
    thesis = dedupe_sentences(pool[:6], 2, min_words=0)
    remaining = [s for s in pool if s not in thesis]
    args = dedupe_sentences(remaining, 4, min_words=7, prefer_numeric=True)
    return {
        'thesis': thesis,
        'args': args,
        'final': extract_recommendation(text),
    }


def extract_manager_summary(text):
    """Research Manager -> recommendation / intro / numbered swing factors."""
    rec_m = re.search(r'\*\*Recommendation\*\*:\s*([A-Za-z]+)', text)
    recommendation = rec_m.group(1).upper() if rec_m else (extract_recommendation(text) or 'HOLD')

    rat_m = re.search(r'\*\*Rationale\*\*:?\s*(.*?)(?=\n\n\*\*\d+\.|\Z)', text, re.DOTALL)
    intro = extract_sentences(rat_m.group(1))[:2] if rat_m else []

    swing = []
    for m in re.finditer(r'\*\*\d+\.\s*([^*\n]+?)\*\*', text):
        swing.append(m.group(1).strip())
        if len(swing) >= 3:
            break

    return {'recommendation': recommendation, 'intro': intro, 'swing': swing}


def extract_technical(text):
    """Market analyst report -> summary table (if data available) or note."""
    no_data = bool(re.search(r'Data Availability Assessment', text, re.IGNORECASE))
    rec = extract_recommendation(text)

    if no_data:
        reason_m = re.search(r'\*\*Reasoning:?\*\*\s*(.*?)(?=\n\n|\Z)', text, re.DOTALL)
        note = extract_sentences(reason_m.group(1))[:2] if reason_m else []
        return {'available': False, 'recommendation': rec, 'note': note, 'table': []}

    table_rows = []
    m = re.search(r'#{2,4}[^\n]*Summary Table[^\n]*\n+((?:\|.*\n?)+)', text, re.IGNORECASE)
    if m:
        lines = [l for l in m.group(1).strip().split('\n') if l.strip().startswith('|')]
        table_rows = parse_tbl(lines)

    em = re.search(r'#{2,4}[^\n]*Executive Summary[^\n]*\n+(.*?)(?=\n#{2,4}|\Z)', text,
                    re.IGNORECASE | re.DOTALL)
    intro = extract_sentences(em.group(1))[:2] if em else []
    return {'available': True, 'recommendation': rec, 'intro': intro, 'table': table_rows}


def extract_fundamentals_summary(text):
    """Fundamentals analyst report -> condensed summary table + key insights."""
    table_rows = []
    m = re.search(r'#{1,2}[^\n]*Summary Table[^\n]*\n+((?:\|.*\n?)+)', text, re.IGNORECASE)
    if m:
        lines = [l for l in m.group(1).strip().split('\n') if l.strip().startswith('|')]
        table_rows = parse_tbl(lines)
        if len(table_rows) > 11:
            table_rows = [table_rows[0]] + table_rows[1:11]

    insights = []
    for m2 in re.finditer(r'\*\*Key Insight:\*\*\s*(.+?)(?=\n\n|\Z)', text, re.DOTALL):
        s = extract_sentences(m2.group(1))
        if s:
            insights.append(s[0])

    if not insights:
        # Fallback: "[OK]/[!] **Label:** text" strength/concern bullet lines
        # (cleaned from the source report's checkmark/warning markers)
        for m3 in re.finditer(r'^\[(?:OK|NO|!)\]\s*\*\*([^*:]+):\*\*\s*(.+)$', text, re.MULTILINE):
            insights.append(f'{m3.group(1).strip()}: {m3.group(2).strip()}')

    insights = dedupe_sentences(insights, 5, min_words=4)

    mcap_m = (re.search(r'\*\*Market Capitalization:\*\*\s*([^\n]+)', text)
              or re.search(r'\*\*Market Cap:\*\*\s*([^\n]+)', text))
    return {
        'table': table_rows,
        'insights': insights,
        'recommendation': extract_recommendation(text),
        'market_cap': mcap_m.group(1).strip() if mcap_m else '',
    }


def extract_sentiment_summary(text):
    """Sentiment analyst report -> score / sources / themes / catalysts."""
    m = re.search(
        r'\*\*Overall Sentiment:\*\*\s*\*{0,2}([^*\n(]+?)\*{0,2}\s*\(Score:\s*([^)]+)\)', text)
    label = m.group(1).strip() if m else ''
    score = m.group(2).strip() if m else ''

    m2 = re.search(r'\*\*Confidence:\*\*\s*([^\n]+)', text)
    confidence = m2.group(1).strip() if m2 else ''

    sources = []
    for sm in re.finditer(
            r'#{2,4}\s*\d+\.\s*([^\n]+)\n((?:.*\n?)*?)(?=\n#{2,4}\s*\d+\.|\n---|\Z)', text):
        name = sm.group(1).strip()
        st_m = re.search(r'\*\*Status:?\*\*\s*([^\n]+)', sm.group(2))
        sources.append((name, st_m.group(1).strip() if st_m else ''))
        if len(sources) >= 4:
            break

    themes = []
    tm = re.search(r'#{1,2}\s*Dominant Narrative Themes\s*\n+(.*?)(?=\n#{1,2}|\n---|\Z)',
                    text, re.DOTALL)
    if tm:
        themes = extract_sentences(tm.group(1))[:3]

    cr = []
    cm = re.search(r'#{1,2}\s*Catalysts and Risks.*?\n+(.*?)(?=\n#{1,2}|\n---|\Z)',
                    text, re.DOTALL)
    if cm:
        cr = extract_sentences(cm.group(1))[:3]

    return {'label': label, 'score': score, 'confidence': confidence,
            'sources': sources, 'themes': themes, 'catalysts_risks': cr}


def extract_news_summary(text):
    """News/macro report -> macro themes + data table + risks/opportunities."""
    themes = []
    for m in re.finditer(
            r'^#{1,3}\s*(\d+)\.\s*(.+?)\s*$\n(.*?)(?=\n#{1,3}\s*\d+\.|\Z)',
            text, re.DOTALL | re.MULTILINE):
        if int(m.group(1)) > 4:
            continue
        title = re.sub(r'\*+', '', m.group(2)).strip()
        sents = extract_sentences(m.group(3))
        if sents:
            themes.append((title, sents[0]))

    table_rows = []
    m = re.search(
        r'#{1,2}\s*\d*\.?\s*Key Data Points? Summary Table\s*\n+((?:\|.*\n?)+)',
        text, re.IGNORECASE)
    if m:
        lines = [l for l in m.group(1).strip().split('\n') if l.strip().startswith('|')]
        table_rows = parse_tbl(lines)
        if len(table_rows) > 9:
            table_rows = [table_rows[0]] + table_rows[1:9]

    risks, opps = [], []
    rm = re.search(
        r'#{1,2}\s*\d*\.?\s*Key Risks\s*&?\s*Opportunities\s*\n(.*?)(?=\n#{1,2}|\Z)',
        text, re.DOTALL | re.IGNORECASE)
    if rm:
        block = rm.group(1)
        risk_m = re.search(r'#{0,3}\s*Risks?\s*\n(.*?)(?=\n#{0,3}\s*Opportunit|\Z)',
                            block, re.DOTALL | re.IGNORECASE)
        opp_m = re.search(r'#{0,3}\s*Opportunit\w*\s*\n(.*)', block, re.DOTALL | re.IGNORECASE)

        def _items(blk):
            out = []
            for l in blk.split('\n'):
                s = l.strip()
                if s and (s[0].isdigit() or s.startswith(('-', '*'))):
                    out.append(re.sub(r'^(?:\d+\.|[-*])\s*', '', s))
            return out

        if risk_m:
            risks = _items(risk_m.group(1))
        if opp_m:
            opps = _items(opp_m.group(1))

    return {'themes': themes[:4], 'table': table_rows, 'risks': risks[:4], 'opportunities': opps[:4]}


def extract_trader_plan(text):
    """Trader's plan -> action / reasoning / position sizing / final call."""
    action_m = re.search(r'\*\*Action\*\*:\s*([^\n]+)', text)
    reasoning_m = re.search(r'\*\*Reasoning\*\*:\s*(.*?)(?=\n\n\*\*|\Z)', text, re.DOTALL)
    sizing_m = re.search(r'\*\*Position Sizing\*\*:\s*(.*?)(?=\n\nFINAL|\Z)', text, re.DOTALL)
    return {
        'action': action_m.group(1).strip() if action_m else '',
        'reasoning': extract_sentences(reasoning_m.group(1))[:4] if reasoning_m else [],
        'sizing': re.sub(r'\s+', ' ', sizing_m.group(1)).strip() if sizing_m else '',
        'final': extract_recommendation(text),
    }


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


# ── Distilled-report PDF builder ──────────────────────────────────────────────

def build_distilled_pdf(root: Path, ticker: str, date: str, sections: dict, base: Path):
    """Build the 5-8 page distilled investment research report."""
    _check_reportlab()
    (colors, letter, ParagraphStyle, inch,
     TA_CENTER, TA_LEFT, TA_JUSTIFY,
     SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
     PageBreak, HRFlowable) = _rl_imports()

    safe_ticker = re.sub(r'[\\/:*?"<>|]', '_', ticker)
    time_str = datetime.now().strftime('%H-%M-%S')
    filename = f'{safe_ticker}_{date}_{time_str}.pdf'

    primary_dir = base / 'reports' / safe_ticker / date
    primary_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = primary_dir / filename
    latest_dir = base / 'reports' / 'latest'
    latest_dir.mkdir(parents=True, exist_ok=True)

    NAVY  = colors.HexColor('#1B2B4B')
    GOLD  = colors.HexColor('#C09B3A')
    GRAY  = colors.HexColor('#6C757D')
    LGRAY = colors.HexColor('#F8F9FA')
    MGRAY = colors.HexColor('#CED4DA')
    DGRAY = colors.HexColor('#343A40')

    VERDICT_COLORS = {
        rec: (colors.HexColor(fg), colors.HexColor(bg))
        for rec, (fg, bg) in _VERDICT_THEME.items()
    }

    def draw_footer(canvas, doc):
        canvas.saveState()
        w, _ = letter
        canvas.setStrokeColor(MGRAY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75*inch, 0.65*inch, w - 0.75*inch, 0.65*inch)
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75*inch, 0.45*inch,
                          f'{ticker}  |  {date}  |  Kevin Cheng Investment Research')
        canvas.drawRightString(w - 0.75*inch, 0.45*inch, f'Page {doc.page}')
        canvas.restoreState()

    def ps(name, **kw):
        from reportlab.lib.styles import getSampleStyleSheet
        parent = kw.pop('parent', getSampleStyleSheet()['Normal'])
        return ParagraphStyle(name, parent=parent, **kw)

    body  = ps('d_body', fontSize=9.5, leading=14, spaceAfter=6,
               alignment=TA_JUSTIFY, textColor=colors.HexColor('#212529'))
    blt   = ps('d_blt',  parent=body, alignment=TA_LEFT,
               leftIndent=18, firstLineIndent=0, spaceAfter=4)
    sec_h = ps('d_sech', fontSize=16, fontName='Helvetica-Bold',
               textColor=NAVY, leading=20, spaceBefore=8, spaceAfter=8)
    h2s   = ps('d_h2',   fontSize=12, fontName='Helvetica-Bold',
               textColor=NAVY, leading=15, spaceBefore=10, spaceAfter=4)
    h3s   = ps('d_h3',   fontSize=10.5, fontName='Helvetica-Bold',
               textColor=DGRAY, leading=13, spaceBefore=7, spaceAfter=3)
    tbl_h = ps('d_tblh', fontSize=8, fontName='Helvetica-Bold',
               textColor=colors.white, leading=10, alignment=TA_CENTER)
    tbl_c = ps('d_tblc', fontSize=8, leading=10, spaceAfter=0)
    small = ps('d_small', fontSize=8.5, leading=12, textColor=GRAY)

    def rl_table(rows, col_widths=None):
        if not rows:
            return []
        ncols = max(len(r) for r in rows)
        cw = col_widths or [(7.0*inch)/ncols]*ncols
        data = [[Paragraph(md2rl(c), tbl_h) for c in rows[0]]]
        for row in rows[1:]:
            pad = (row + ['']*ncols)[:ncols]
            data.append([Paragraph(md2rl(c), tbl_c) for c in pad])
        t = Table(data, colWidths=cw, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',     (0,0),(-1,0),  DGRAY),
            ('ROWBACKGROUNDS', (0,1),(-1,-1), [colors.white, LGRAY]),
            ('GRID',           (0,0),(-1,-1), 0.4, MGRAY),
            ('TOPPADDING',     (0,0),(-1,-1), 4),
            ('BOTTOMPADDING',  (0,0),(-1,-1), 4),
            ('LEFTPADDING',    (0,0),(-1,-1), 5),
            ('RIGHTPADDING',   (0,0),(-1,-1), 5),
            ('VALIGN',         (0,0),(-1,-1), 'TOP'),
        ]))
        return [Spacer(1,4), t, Spacer(1,8)]

    def bullets(items, style=blt):
        return [Paragraph(f'&#x2022;&nbsp;{md2rl(s)}', style) for s in items]

    def bar(colour, height=3):
        t = Table([['']], colWidths=[7*inch], rowHeights=[height])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0),(-1,-1), colour),
            ('TOPPADDING', (0,0),(-1,-1), 0),
            ('BOTTOMPADDING', (0,0),(-1,-1), 0),
        ]))
        return t

    def section_header(title):
        return [Paragraph(title, sec_h),
                HRFlowable(width='100%', thickness=1.5, color=NAVY, spaceAfter=10)]

    # ── Extract distilled content from each section ──────────────────────────
    company_name = extract_company_name(sections['fundamentals'])
    data_src = (extract_data_sources_header(sections['market'])
                or extract_data_sources_header(sections['fundamentals'])
                or extract_data_sources_header(sections['decision']))

    if sections['decision']:
        verdict = extract_verdict(sections['decision'])
    elif sections['manager']:
        ms = extract_manager_summary(sections['manager'])
        verdict = {'recommendation': ms['recommendation'], 'rationale': ms['intro'], 'actions': []}
    else:
        verdict = {'recommendation': 'HOLD', 'rationale': [], 'actions': []}

    cons_risks = extract_risk_view(sections['conservative'], 3)
    aggr_risks = extract_risk_view(sections['aggressive'], 3)
    neut_risks = extract_risk_view(sections['neutral'], 2)

    bull_view = extract_research_view(sections['bull'], 'Bull') if sections['bull'] else None
    bear_view = extract_research_view(sections['bear'], 'Bear') if sections['bear'] else None
    mgr_summary = extract_manager_summary(sections['manager']) if sections['manager'] else None

    tech = extract_technical(sections['market']) if sections['market'] else None
    fund = extract_fundamentals_summary(sections['fundamentals']) if sections['fundamentals'] else None
    sent = extract_sentiment_summary(sections['sentiment']) if sections['sentiment'] else None
    news = extract_news_summary(sections['news']) if sections['news'] else None
    trader = extract_trader_plan(sections['trader']) if sections['trader'] else None

    # ── PAGE 1: Cover ──────────────────────────────────────────────────────
    story = [
        Spacer(1, 1.3*inch), bar(NAVY, 3), Spacer(1, 0.25*inch),
        Paragraph('INVESTMENT RESEARCH REPORT',
                  ps('d_ct', fontSize=24, fontName='Helvetica-Bold',
                     textColor=NAVY, leading=30, alignment=TA_CENTER)),
        Spacer(1, 0.15*inch),
        Paragraph(ticker,
                  ps('d_ck', fontSize=38, fontName='Helvetica-Bold',
                     textColor=GOLD, leading=46, alignment=TA_CENTER)),
    ]
    if company_name:
        story.append(Paragraph(esc(company_name),
                  ps('d_cn', fontSize=14, textColor=DGRAY, leading=18, alignment=TA_CENTER)))
    story += [
        Spacer(1, 0.1*inch), bar(GOLD, 2), Spacer(1, 0.25*inch),
        Paragraph(f'Analysis Date: {date}',
                  ps('d_cd', fontSize=13, textColor=GRAY, leading=17, alignment=TA_CENTER)),
        Spacer(1, 1.0*inch),
        Paragraph('Prepared for: Kevin Cheng',
                  ps('d_cp', fontSize=12, textColor=GRAY, leading=15, alignment=TA_CENTER)),
        Spacer(1, 0.08*inch),
        Paragraph('Powered by TradingAgents',
                  ps('d_cq', fontSize=10, textColor=GRAY, leading=13, alignment=TA_CENTER)),
        Paragraph('Multi-Agent LLM Financial Analysis -- Distilled Report',
                  ps('d_cr', fontSize=10, textColor=MGRAY, leading=13, alignment=TA_CENTER)),
    ]
    if data_src:
        story.append(Spacer(1, 0.5*inch))
        story.append(Paragraph('Data Sources',
                  ps('d_dsh', fontSize=10, fontName='Helvetica-Bold',
                     textColor=GRAY, leading=13, alignment=TA_CENTER)))
        for line in data_src:
            story.append(Paragraph(esc(line),
                  ps('d_ds', fontSize=8.5, textColor=GRAY, leading=12, alignment=TA_CENTER)))
    story.append(PageBreak())

    # ── PAGE 2: Investment Verdict ────────────────────────────────────────
    rec = verdict['recommendation']
    rec_fg, rec_bg = VERDICT_COLORS.get(rec, VERDICT_COLORS['HOLD'])

    story += section_header('Investment Verdict')

    rec_st = ps('d_rec', fontSize=28, fontName='Helvetica-Bold',
                textColor=colors.white, leading=34, alignment=TA_CENTER)
    rec_tbl = Table([[Paragraph(rec, rec_st)]], colWidths=[7.0*inch])
    rec_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,-1), rec_fg),
        ('TOPPADDING', (0,0),(-1,-1), 14),
        ('BOTTOMPADDING', (0,0),(-1,-1), 14),
    ]))
    story += [rec_tbl, Spacer(1, 12)]

    if verdict['rationale']:
        story.append(Paragraph('Rationale', h2s))
        story.append(Paragraph(md2rl(' '.join(verdict['rationale'])), body))

    if verdict['actions']:
        story.append(Paragraph('Strategic Actions', h2s))
        story += bullets(verdict['actions'])

    story.append(Spacer(1, 8))
    story.append(Paragraph('Key Risks (Risk Management Team)', h2s))
    if cons_risks:
        story.append(Paragraph('Conservative View', h3s))
        story += bullets(cons_risks)
    if aggr_risks:
        story.append(Paragraph('Aggressive View', h3s))
        story += bullets(aggr_risks)
    if neut_risks:
        story.append(Paragraph('Neutral View', h3s))
        story += bullets(neut_risks)

    story.append(PageBreak())

    # ── PAGE 3: Research Team Views ───────────────────────────────────────
    story += section_header('Research Team Views')

    if bull_view:
        story.append(Paragraph('Bull Case', h2s))
        if bull_view['thesis']:
            story.append(Paragraph(md2rl(' '.join(bull_view['thesis'])), body))
        if bull_view['args']:
            story.append(Paragraph('Supporting Arguments', h3s))
            story += bullets(bull_view['args'])

    if bear_view:
        story.append(Paragraph('Bear Case', h2s))
        if bear_view['thesis']:
            story.append(Paragraph(md2rl(' '.join(bear_view['thesis'])), body))
        if bear_view['args']:
            story.append(Paragraph('Supporting Arguments', h3s))
            story += bullets(bear_view['args'])

    if mgr_summary:
        story.append(Paragraph('Research Manager Summary', h2s))
        story.append(Paragraph(f"Decision: <b>{mgr_summary['recommendation']}</b>", body))
        if mgr_summary['intro']:
            story.append(Paragraph(md2rl(' '.join(mgr_summary['intro'])), body))
        if mgr_summary['swing']:
            story.append(Paragraph('Key Swing Factors', h3s))
            story += bullets(mgr_summary['swing'])

    story.append(PageBreak())

    # ── PAGE 4: Analyst Reports Summary ───────────────────────────────────
    story += section_header('Analyst Reports Summary')

    story.append(Paragraph('Technical Analysis', h2s))
    if tech and tech['available']:
        if tech['intro']:
            story.append(Paragraph(md2rl(' '.join(tech['intro'])), body))
        if tech['table']:
            story += rl_table(tech['table'])
        if tech['recommendation']:
            story.append(Paragraph(f"Technical Signal: <b>{tech['recommendation']}</b>", body))
    elif tech:
        story.append(Paragraph(
            'Price/technical data was unavailable for this ticker from the data provider.', small))
        if tech['note']:
            story.append(Paragraph(md2rl(' '.join(tech['note'])), body))
    else:
        story.append(Paragraph('No technical analysis available.', small))

    story.append(Paragraph('Fundamental Analysis', h2s))
    if fund:
        if fund['market_cap']:
            story.append(Paragraph(f"Market Capitalization: <b>{esc(fund['market_cap'])}</b>", body))
        if fund['table']:
            story += rl_table(fund['table'])
        if fund['insights']:
            story.append(Paragraph('Key Insights', h3s))
            story += bullets(fund['insights'])
        if fund['recommendation']:
            story.append(Paragraph(f"Fundamentals Signal: <b>{fund['recommendation']}</b>", body))
    else:
        story.append(Paragraph('No fundamentals analysis available.', small))

    story.append(Paragraph('Sentiment Analysis', h2s))
    if sent:
        if sent['label']:
            story.append(Paragraph(
                f"Overall Sentiment: <b>{esc(sent['label'])}</b> "
                f"(Score: {esc(sent['score'])}) | Confidence: {esc(sent['confidence'])}", body))
        if sent['sources']:
            rows = [['Source', 'Status']] + [[s[0], s[1]] for s in sent['sources']]
            story += rl_table(rows, col_widths=[2.3*inch, 4.7*inch])
        if sent['themes']:
            story.append(Paragraph('Dominant Themes', h3s))
            story += bullets(sent['themes'])
        if sent['catalysts_risks']:
            story.append(Paragraph('Catalysts & Risks', h3s))
            story += bullets(sent['catalysts_risks'])
    else:
        story.append(Paragraph('No sentiment analysis available.', small))

    story.append(PageBreak())

    # ── PAGE 5: Market & News Context ─────────────────────────────────────
    story += section_header('Market & News Context')

    story.append(Paragraph('News & Macro Analysis', h2s))
    if news and (news['themes'] or news['table']):
        for title, sent_text in news['themes']:
            story.append(Paragraph(esc(title), h3s))
            story.append(Paragraph(md2rl(sent_text), body))
        if news['table']:
            story.append(Paragraph('Key Data Points', h3s))
            story += rl_table(news['table'])
    else:
        story.append(Paragraph('No news/macro analysis available.', small))

    story.append(Paragraph('Catalysts & Risks From News', h2s))
    if news and (news['risks'] or news['opportunities']):
        if news['opportunities']:
            story.append(Paragraph('Opportunities', h3s))
            story += bullets(news['opportunities'])
        if news['risks']:
            story.append(Paragraph('Risks', h3s))
            story += bullets(news['risks'])
    else:
        story.append(Paragraph('None identified.', small))

    # ── PAGES 6+: Trader's Action Plan ────────────────────────────────────
    if trader and (trader['action'] or trader['reasoning'] or trader['sizing']):
        story.append(PageBreak())
        story += section_header("Trader's Action Plan")
        if trader['action']:
            story.append(Paragraph(f"Action: <b>{esc(trader['action'])}</b>", body))
        if trader['reasoning']:
            story.append(Paragraph('Reasoning', h3s))
            story.append(Paragraph(md2rl(' '.join(trader['reasoning'])), body))
        if trader['sizing']:
            story.append(Paragraph('Position Sizing', h3s))
            story.append(Paragraph(md2rl(trader['sizing']), body))
        if trader['final']:
            story.append(Spacer(1, 8))
            story.append(Paragraph(f"FINAL TRANSACTION PROPOSAL: <b>{trader['final']}</b>", h2s))

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

def _run_distilled(root: Path, ticker: str, date: str, base: Path):
    sections = gather_distilled_sections(root)
    print(f'Source  : {root}')
    primary, latest = build_distilled_pdf(root, ticker, date, sections, base)
    archive = archive_full_debate(root, ticker, date, primary.parent)
    print(f'PDF     : {primary}')
    print(f'Latest  : {latest}')
    print(f'Archive : {archive}')
    open_in_edge(primary)


if __name__ == '__main__':
    base = Path(__file__).parent

    # Specific report root or section file supplied
    if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        rp = Path(sys.argv[1])
        if not rp.exists():
            sys.exit(f'ERROR: {rp} not found')
        root = rp if rp.is_dir() else rp.parent
        if root.name in ('1_analysts', '2_research', '3_trading', '4_risk', '5_portfolio'):
            root = root.parent
        ticker, date = _extract_ticker_date(root, base)
        _run_distilled(root, ticker, date, base)
        sys.exit(0)

    # Auto-find the most recently-touched report directory
    root, ticker, date = find_report_root(base)
    if root is not None:
        sections = gather_distilled_sections(root)
        if sum(1 for v in sections.values() if v.strip()) >= 3:
            _run_distilled(root, ticker, date, base)
            sys.exit(0)

    # Fall back to a partial/incomplete-run PDF (e.g. crashed analysis)
    ticker, date, partial_sections = find_partial_sections(base)
    if partial_sections:
        print(f'No complete report found — building partial PDF from '
              f'{len(partial_sections)} section(s)...')
        primary, latest = build_partial_pdf(partial_sections, ticker, date, base)
        print(f'PDF     : {primary}')
        print(f'Latest  : {latest}')
        open_in_edge(primary)
        sys.exit(0)

    sys.exit(
        'ERROR: No report files found in outputs/ or reports/\n'
        'Run TradingAgents and press Y at the "Save report?" prompt.\n'
        'If Anthropic credit ran out, check platform.anthropic.com/billing')
