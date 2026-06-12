#!/usr/bin/env python3
"""
convert_report.py
Converts TradingAgents complete_report.md (or partial section files) to PDF.
Usage:
    python convert_report.py                     # auto-finds best report
    python convert_report.py path/to/report.md   # specific complete_report.md
"""
import os, sys, re, glob, shutil, subprocess, json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / '.env')


# ── Text cleaning ────────────────────────────────────────────────────────────

_REPLACE = [
    ('✅','[OK]'), ('❌','[NO]'), ('✗','[NO]'), ('✓','[OK]'),
    ('⚠️','[!]'), ('⚠','[!]'), ('✔','[OK]'),
    ('→','->'), ('←','<-'), ('↑','^'), ('↓','v'),
    ('★','*'), ('☆','*'), ('•','-'),
    ('‘',"'"), ('’',"'"), ('“','"'), ('”','"'),
    ('–','-'), ('—','--'), ('…','...'), (' ',' '),
    ('‑','-'), ('‒','-'),
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


# ── Professional-tone normalization (FIX2) ──────────────────────────────────
# First-person/theatrical framings to strip wholesale. Any sentence
# containing one of these (case-insensitive) is dropped entirely, since
# these phrases mark debate/rhetoric rather than analytical content.
_THEATRICAL_PHRASES = [
    "i think", "i believe", "let me", "i appreciate", "i find", "i must",
    "i have to say", "i get it", "make no mistake", "let's be",
    "the fact is", "what we have here", "pull back the curtain",
    "rearview mirror", "road ahead", "house of cards", "beautiful story",
    "war chest", "rocket ship", "textbook", "cut through the noise",
    "my opponent says", "as i argued", "you said",
    "looks scary on a spreadsheet", "dismantle your argument",
    "i recommend", "i've", "i'm ", "after an exhaustive debate",
    "my decision", "my view", "my analysis", "my recommendation",
    "my conviction", "the three critical points",
    "oh, by the way", "by the way", "i acknowledge", "to be fair",
    "honestly,", "frankly,", "i'd", "i would", "i'll", "i will",
    "in my opinion", "to be clear",
]
_THEATRICAL_RE = re.compile(
    '|'.join(re.escape(p) for p in _THEATRICAL_PHRASES), re.IGNORECASE)

# Debate-framing references ("you/your", "my opponent", "the bull/bear case")
# stripped from research/risk-team bullets so each side's memo reads as a
# standalone analysis with no reference to the other side.
_DEBATE_REF_RE = re.compile(
    r"\b(?:you|your|you're|yours?|the other side|the bull case|the bear case)\b",
    re.IGNORECASE)


def _drop_debate_refs(sentences):
    """Remove sentences that reference the opposing analyst/side."""
    return [s for s in sentences if not _DEBATE_REF_RE.search(s)]

# Leading conjunctions/hedges to strip from sentence starts (rule: "no
# conjunctions like but/however/yet at sentence start").
_LEADING_CONJ_RE = re.compile(
    r'^(?:But|However|Yet|And|So|Nevertheless|Nonetheless|Moreover|Although|Though|Still),?\s+',
    re.IGNORECASE)

# Leading "1. " / "2) " numbered-list markers left over after collapsing a
# markdown list item to a single sentence.
_LEADING_NUM_RE = re.compile(r'^\d+[\.\)]\s*')


def _polish_sentence(s):
    """Strip leading conjunctions/hedges/list-numbers and re-capitalize."""
    s = _LEADING_NUM_RE.sub('', s)
    s = _LEADING_CONJ_RE.sub('', s)
    if s:
        s = s[0].upper() + s[1:]
    return s


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
        if not s or s.endswith('?'):  # drop empties and rhetorical questions
            continue
        if _THEATRICAL_RE.search(s):  # drop first-person/theatrical sentences
            continue
        out.append(_polish_sentence(s))
    return out


def _word_set(s):
    return set(re.findall(r'[a-z0-9]+', s.lower()))


def dedupe_sentences(sentences, max_n=6, min_words=4, prefer_numeric=False,
                      require_numeric=False):
    """Keep the first occurrence of each roughly-distinct claim.

    With prefer_numeric=True, sentences containing a number/%/$ (the
    factual data points the distillation rules say to keep) are
    considered before purely qualitative ones.

    With require_numeric=True, sentences without a number/%/$ are dropped
    outright (each bullet must state a specific metric), unless that would
    leave nothing to choose from.
    """
    candidates = [s for s in sentences if len(_word_set(s)) >= min_words]
    if require_numeric:
        numeric = [s for s in candidates if re.search(r'[\d$%]', s)]
        if numeric:
            candidates = numeric
    elif prefer_numeric:
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
        r'\*\*Rationale\*\*:?\s*(.*?)(?=\n\n\*\*Strategic|\Z)',
        text, re.DOTALL)
    rationale = []
    if rat_m:
        rat_sents = extract_sentences(rat_m.group(1))
        rationale = dedupe_sentences(rat_sents, max_n=3, require_numeric=True)
        if not rationale:
            rationale = dedupe_sentences(rat_sents, max_n=2)

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
    """Conservative / Aggressive / Neutral risk-team round-1 view -> bullets.

    Each bullet must state a specific number/metric (rule: "must contain at
    least one specific number/metric"); backfilled with qualitative
    sentences only if too few numeric ones are available.
    """
    if not text:
        return []
    sentences = extract_sentences(first_round(text))
    clean = _drop_debate_refs(sentences)
    pool = clean if clean else sentences
    out = dedupe_sentences(pool, max_n, min_words=7, require_numeric=True)
    if len(out) < max_n:
        for s in dedupe_sentences(pool, max_n, min_words=7):
            if s not in out:
                out.append(s)
            if len(out) >= max_n:
                break
    return out


def extract_research_view(text, label):
    """Bull / Bear researcher round-1 -> 4-6 memo-style bullet points.

    Each bullet is a single, complete, third-person sentence stating a
    specific number/metric and what it means for the thesis -- no debate
    framing, no reference to the other side.
    """
    round1 = first_round(text)
    quotes = agent_quotes(round1, label)
    sentences = extract_sentences(quotes)
    substantive = [s for s in sentences if len(_word_set(s)) >= 7]
    pool = substantive if substantive else sentences
    clean = _drop_debate_refs(pool)
    pool = clean if clean else pool

    bullets = dedupe_sentences(pool, 6, min_words=7, require_numeric=True)
    if len(bullets) < 4:
        for s in dedupe_sentences(pool, 6, min_words=7):
            if s not in bullets:
                bullets.append(s)
            if len(bullets) >= 4:
                break

    return {
        'bullets': bullets[:6],
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


# ── Risk table: "| Risk Factor | Severity | Evidence | Significance |" ──────
# Templated significance rationale, keyed by keywords found in the risk
# factor name or evidence cell. Falls back to a severity-based statement.
_RISK_SIGNIFICANCE_RULES = [
    (r'negative equity|stockholders.? deficit',
     "Liabilities exceed assets, raising solvency concerns absent further capital raises."),
    (r'cash flow',
     "Operations consume cash, requiring external financing until the business reaches breakeven."),
    (r'equity funding|dilution|stock issuance|share issuance',
     "Reliance on share issuance to fund operations dilutes existing shareholders."),
    (r'operating loss',
     "Core business is not yet self-sustaining; the trend in this figure is central to the thesis."),
    (r'earnings volatility|investment|securities',
     "Non-operating items can obscure the underlying trajectory of the core business."),
    (r'cash runway|runway',
     "Bounds the timeline before additional financing or breakeven is required."),
    (r'liquidity|current ratio',
     "A ratio below 1.0 signals potential difficulty meeting near-term obligations."),
    (r'leverage|debt',
     "Elevated leverage increases sensitivity to financing-cost or refinancing shocks."),
    (r'concentration',
     "Loss of a key customer or supplier could disproportionately affect revenue."),
    (r'valuation',
     "Elevated multiples leave limited room for error if growth decelerates."),
    (r'competition|competitive',
     "Increased competitive pressure could compress margins or market share."),
    (r'regulatory|regulation',
     "Adverse regulatory action could impose costs or restrict operations."),
]


def _risk_significance(factor, severity, evidence):
    """Return a one-sentence rationale for why this risk matters."""
    haystack = f'{factor} {evidence}'.lower()
    for pattern, sig in _RISK_SIGNIFICANCE_RULES:
        if re.search(pattern, haystack):
            return sig
    sev = severity.lower()
    if 'high' in sev:
        return "High-severity factor with direct bearing on near-term valuation and solvency."
    if 'medium' in sev:
        return "Moderate factor that warrants monitoring but is not yet thesis-changing."
    return "Lower-severity factor unlikely to be thesis-changing on its own."


def extract_key_risks_table(text):
    """Pull the fundamentals report's '## Key Risks' table
    (Risk Factor | Severity | Details) and append a Significance column."""
    if not text:
        return []
    m = re.search(r'#{1,3}[^\n]*Key Risks?[^\n]*\n+((?:\|.*\n?)+)', text, re.IGNORECASE)
    if not m:
        return []
    lines = [l for l in m.group(1).strip().split('\n') if l.strip().startswith('|')]
    rows = parse_tbl(lines)
    if len(rows) < 2:
        return []

    out = [['Risk Factor', 'Severity', 'Evidence', 'Significance']]
    for row in rows[1:]:
        factor, severity, evidence = (row + ['', '', ''])[:3]
        out.append([factor, severity, evidence,
                     _risk_significance(factor, severity, evidence)])
    return out


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


def _parse_news_tbl(text, heading_pat):
    """Extract a markdown table after a section heading matched by heading_pat."""
    m = re.search(heading_pat + r'\s*\n+((?:\|.*\n?)+)', text, re.IGNORECASE)
    if not m:
        return []
    lines = [l for l in m.group(1).strip().split('\n') if l.strip().startswith('|')]
    return parse_tbl(lines)


def extract_news_summary(text):
    """News/macro report -> macro themes + data table + actionable tables + risks/opportunities."""
    # Tables that should be rendered separately (skip in themes loop)
    _TABLE_SECTION_RE = re.compile(
        r'actionable\s+insights?|summary\s+table|key\s+data\s+points?'
        r'|key\s+(?:bullish|bearish)|bullish\s+catalysts?|bearish\s+risks?',
        re.IGNORECASE)

    themes = []
    for m in re.finditer(
            r'^#{1,3}\s*(\d+)\.\s*(.+?)\s*$\n(.*?)(?=\n#{1,3}\s*\d+\.|\Z)',
            text, re.DOTALL | re.MULTILINE):
        if int(m.group(1)) > 4:
            continue
        title = re.sub(r'\*+', '', m.group(2)).strip()
        if re.search(r'key risks?\s*&?\s*opportunit', title, re.IGNORECASE):
            continue  # surfaced separately below
        if _TABLE_SECTION_RE.search(title):
            continue  # these sections are rendered as proper tables
        # Only take sentences from non-table content
        section_text = m.group(3)
        # Skip paragraphs that are purely table markup
        non_table = re.sub(r'(?m)^\|.*\|[ \t]*$', '', section_text)
        sents = extract_sentences(non_table)
        if sents:
            themes.append((title, sents[0]))

    # Key Data Points summary table
    table_rows = []
    m = re.search(
        r'#{1,2}\s*\d*\.?\s*(?:Summary\s+Table\s+of\s+)?Key\s+Data\s+Points?\s*(?:Summary\s+Table)?\s*\n+((?:\|.*\n?)+)',
        text, re.IGNORECASE)
    if not m:
        m = re.search(r'#{1,2}\s*\d*\.?\s*Key Data Points? Summary Table\s*\n+((?:\|.*\n?)+)',
                      text, re.IGNORECASE)
    if m:
        lines = [l for l in m.group(1).strip().split('\n') if l.strip().startswith('|')]
        table_rows = parse_tbl(lines)
        if len(table_rows) > 13:
            table_rows = [table_rows[0]] + table_rows[1:13]

    # Actionable Insights table (Theme / Detail / Impact)
    actionable_table = _parse_news_tbl(
        text, r'#{1,3}[^\n]*(?:\d+\.\s*)?Actionable\s+Insights?(?:\s*&[^\n]*)?')
    if len(actionable_table) > 10:
        actionable_table = [actionable_table[0]] + actionable_table[1:10]

    # Key Bullish Catalysts table
    bullish_table = _parse_news_tbl(text, r'#{1,3}[^\n]*(?:Key\s+)?Bullish\s+Catalysts?')
    if len(bullish_table) > 8:
        bullish_table = [bullish_table[0]] + bullish_table[1:8]

    # Key Bearish Risks table
    bearish_table = _parse_news_tbl(text, r'#{1,3}[^\n]*(?:Key\s+)?Bearish\s+Risks?')
    if len(bearish_table) > 8:
        bearish_table = [bearish_table[0]] + bearish_table[1:8]

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

    return {
        'themes': themes[:4],
        'table': table_rows,
        'actionable': actionable_table,
        'bullish': bullish_table,
        'bearish': bearish_table,
        'risks': risks[:4],
        'opportunities': opps[:4],
    }


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


# ── Investment Dashboard helpers (desk verdicts, price levels, timestamps) ──

def _strip_md(s):
    """Strip markdown bold/italic markers and surrounding whitespace."""
    return re.sub(r'\*+', '', s or '').strip()


def _hkt_timestamp():
    """Return the current time in Hong Kong as 'YYYY-MM-DD HH:MM HKT'.

    This is the single source of truth for any timestamp shown on the
    cover page, headers, or footers of generated reports."""
    return datetime.now(ZoneInfo('Asia/Hong_Kong')).strftime('%Y-%m-%d %H:%M HKT')


_MONTH_MAP = {m: i for i, m in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June',
     'July', 'August', 'September', 'October', 'November', 'December'], start=1)}


def _parse_long_date(s):
    """'June 4, 2026' -> '2026-06-04'. Already-ISO dates pass through unchanged."""
    s = (s or '').strip()
    iso = re.match(r'(\d{4}-\d{2}-\d{2})', s)
    if iso:
        return iso.group(1)
    m = re.search(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', s)
    if not m:
        return None
    month = _MONTH_MAP.get(m.group(1).capitalize())
    if not month:
        return None
    return f'{m.group(3)}-{month:02d}-{int(m.group(2)):02d}'


# ── Verdict normalization (BUY / HOLD / SELL across desk-specific wording) ──

_VERDICT_SYNONYMS = {
    'STRONG BUY': 'BUY', 'BUY': 'BUY', 'OVERWEIGHT': 'BUY', 'ACCUMULATE': 'BUY', 'ADD': 'BUY',
    'HOLD': 'HOLD', 'NEUTRAL': 'HOLD', 'MARKET PERFORM': 'HOLD', 'EQUAL WEIGHT': 'HOLD',
    'SELL': 'SELL', 'UNDERWEIGHT': 'SELL', 'STRONG SELL': 'SELL', 'REDUCE': 'SELL', 'TRIM': 'SELL',
}


def normalize_verdict(raw):
    """Map a desk's free-text rating to BUY / HOLD / SELL, or None if unrecognized."""
    if not raw:
        return None
    key = re.sub(r'[^A-Z ]', '', raw.upper())
    key = re.sub(r'\s+', ' ', key).strip()
    return _VERDICT_SYNONYMS.get(key)


def verdict_hex(verdict):
    """(fg_hex, bg_hex) for a normalized BUY/HOLD/SELL verdict."""
    return _VERDICT_THEME.get(verdict, _VERDICT_THEME['HOLD'])


# ── Latest price / exchange / sector (cover page) ────────────────────────────

# Priority 1: "**Close Price on June 10, 2026:** $891.88" -- price + date together
_PRICE_CLOSE_ON_DATE_RE = re.compile(
    r'\*{0,2}Close\s+Price\s+on\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})\*{0,2}\s*:?\*{0,2}\s*\$?([\d,]+\.?\d*)',
    re.IGNORECASE)

# Priority 2: "**Key Price Levels on 2026-06-10:**\n- Close: $891.88" -- price + date together
_PRICE_LEVELS_CLOSE_RE = re.compile(
    r'\*{0,2}Key\s+Price\s+Levels?\s+on\s+(\d{4}-\d{2}-\d{2}|[A-Za-z]+\s+\d{1,2},?\s*\d{4})\*{0,2}\s*:?\*{0,2}\s*\n'
    r'(?:[^\n]*\n)*?\s*-\s*Close:?\s*\$?([\d,]+\.?\d*)',
    re.IGNORECASE)

# Priority 3: Technical summary table row "| **Price (Last Close)** | $935.89 | ... |"
# or "| **Price (Close)** | $891.88 | ... |"
_PRICE_LAST_CLOSE_TBL_RE = re.compile(
    r'\|\s*\*{0,2}Price\s*\(\s*(?:Last\s+)?Close\s*\)\*{0,2}\s*\|\s*\$?([\d,]+\.?\d*)\s*\|', re.IGNORECASE)

# Priority 4: Inline header "Last Close: $935.89" or "**Last Close:** $935.89"
_PRICE_LAST_CLOSE_HDR_RE = re.compile(
    r'\*{0,2}Last\s+Close[:\s*]+\*{0,2}:?\s*\$?([\d,]+\.?\d*)', re.IGNORECASE)

# Priority 5: "Latest Price" or "Current Price" only as a structured field (table cell
# or bold label at line start) — never as a phrase mid-prose.
_PRICE_STRUCTURED_RE = re.compile(
    r'(?:'
    r'^\*{1,2}(?:Current|Latest)\s+Price\*{0,2}[:*\s]+\$?([\d,]+\.?\d*)'   # bold label at BOL
    r'|\|\s*\*{0,2}(?:Current|Latest)\s+Price\*{0,2}\s*\|\s*\$?([\d,]+\.?\d*)\s*\|'  # table cell
    r')',
    re.IGNORECASE | re.MULTILINE)

_PRICE_DATE_PATTERNS = [
    re.compile(r'Last Complete Session:?\*{0,2}\s*\*{0,2}\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})', re.IGNORECASE),
    re.compile(r'as of\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})', re.IGNORECASE),
    re.compile(r'\*\*Date:?\*{0,2}\s*\*{0,2}\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})', re.IGNORECASE),
    re.compile(r'last\s+trading\s+data:?\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})', re.IGNORECASE),
]

# Reasonable price ranges for validation
_PRICE_RANGE_US  = (1.0, 100_000.0)
_PRICE_RANGE_HK  = (0.1, 2_000.0)


def _validate_price(price_str, ticker=''):
    """Return True if price_str is a plausible stock price."""
    try:
        val = float(price_str.replace(',', ''))
    except (ValueError, AttributeError):
        return False
    if val <= 0:
        return False
    lo, hi = _PRICE_RANGE_HK if re.search(r'\.(HK|SS|SZ)$', ticker, re.IGNORECASE) else _PRICE_RANGE_US
    return lo <= val <= hi


def extract_latest_price(market_text, ticker=''):
    """Return (price_str, date_iso) from the market analyst report.

    Priority order (price + date together first, since these are the most
    unambiguous and come straight from the price_indicators data layer):
    1. "Close Price on <date>: $XXX" header line
    2. "Key Price Levels on <date>:" block with a "- Close: $XXX" bullet
    3. Technical Analysis summary table 'Price (Last Close)' / 'Price (Close)' row
    4. Inline 'Last Close: $XXX' header notation
    5. Structured bold / table-cell 'Current Price' or 'Latest Price' field
    If no valid price found, returns (None, None) and the cover page will
    show 'See Technical Analysis' rather than a wrong number; a warning is
    printed to the console so the gap is visible during report generation.
    """
    if not market_text:
        return None, None

    price = None
    date = None

    # Priority 1: "Close Price on <date>: $XXX" -- price + date together
    m = _PRICE_CLOSE_ON_DATE_RE.search(market_text)
    if m and _validate_price(m.group(2), ticker):
        price, date = m.group(2), _parse_long_date(m.group(1))

    # Priority 2: "Key Price Levels on <date>:\n- Close: $XXX" -- price + date together
    if not price:
        m = _PRICE_LEVELS_CLOSE_RE.search(market_text)
        if m and _validate_price(m.group(2), ticker):
            price, date = m.group(2), _parse_long_date(m.group(1))

    # Priority 3: summary table row
    if not price:
        m = _PRICE_LAST_CLOSE_TBL_RE.search(market_text)
        if m and _validate_price(m.group(1), ticker):
            price = m.group(1)

    # Priority 4: inline header
    if not price:
        m = _PRICE_LAST_CLOSE_HDR_RE.search(market_text)
        if m and _validate_price(m.group(1), ticker):
            price = m.group(1)

    # Priority 5: structured field (bold label at line start or table cell)
    if not price:
        m = _PRICE_STRUCTURED_RE.search(market_text)
        if m:
            candidate = m.group(1) or m.group(2)
            if _validate_price(candidate, ticker):
                price = candidate

    if not date:
        for pat in _PRICE_DATE_PATTERNS:
            dm = pat.search(market_text)
            if dm:
                date = _parse_long_date(dm.group(1))
                break

    if not price:
        print(f'WARNING: could not extract latest price for {ticker or "ticker"} '
              f'from market report -- cover page will show N/A')
    return price, date


def extract_exchange_sector(fundamentals_text, market_text=None):
    """Pull Exchange / Sector / Industry from the fundamentals (preferred) or
    market report header lines."""
    out = {'exchange': None, 'sector': None, 'industry': None}
    patterns = {
        'exchange': re.compile(r'\*\*Exchange:?\*\*\s*([^\n|]+)', re.IGNORECASE),
        'sector': re.compile(r'\*\*Sector:?\*\*\s*([^\n|]+)', re.IGNORECASE),
        'industry': re.compile(r'\*\*Industry:?\*\*\s*([^\n|]+)', re.IGNORECASE),
    }
    for key, pat in patterns.items():
        for text in (fundamentals_text, market_text):
            if not text:
                continue
            m = pat.search(text)
            if m:
                out[key] = _strip_md(m.group(1))
                break
    return out


# ── Desk verdict extractors (Portfolio Manager / Research Manager / Trader) ─

_PM_VERDICT_PATTERNS = [
    re.compile(r'\*\*Final Trading Decision:\s*([A-Za-z ]+?)\*\*', re.IGNORECASE),
    re.compile(r'\*\*Final Trading Decision\*\*:\s*([A-Za-z ]+)', re.IGNORECASE),
    re.compile(r'\*\*Rating\*\*:\s*([A-Za-z ]+)', re.IGNORECASE),
    re.compile(r'\*\*Rating:\s*([A-Za-z ]+?)\*\*', re.IGNORECASE),
    re.compile(r'\*\*Recommendation\*\*:\s*([A-Za-z ]+)', re.IGNORECASE),
    re.compile(r'\*\*Recommendation:\s*([A-Za-z ]+?)\*\*', re.IGNORECASE),
]


def _first_match_word(text, patterns):
    for pat in patterns:
        m = pat.search(text)
        if m:
            return _strip_md(m.group(1))
    return None


_TRADING_DETAIL_RE = re.compile(
    r'\btranche\b|position\s+siz|\bentry\s+price\b|\bstop.?loss\b'
    r'|\b\d+[-–]\d+%\s+of\s+(?:full\s+)?position'
    r'|\bfirst\s+tranche\b|\bsecond\s+tranche\b'
    r'|proposed\s+(?:buy|sell)\s+at\s+\$'
    r'|\bstop\s+is\s+rejected\b'
    r'|\bphased\s+accumulation\b'
    r'|\bpullback\s+to\b.*\$'
    r'|\bdeploy\s+the\s+(?:first|second)\b',
    re.IGNORECASE)


def _pm_clean_sents(text):
    """Extract sentences from PM decision text that are analytical (not
    position-sizing / tranche / entry-price instructions)."""
    sents = extract_sentences(text)
    return [s for s in sents if not _TRADING_DETAIL_RE.search(s)]


def extract_pm_desk(decision_text):
    """Portfolio Manager desk -> verdict / price target / horizon / 1-line reason.

    Reason is extracted from the intro paragraph (text before the first ###
    subsection) so it reflects the overall thesis, not an individual tranche's
    sub-rationale bullet.
    """
    if not decision_text:
        return None
    raw = _first_match_word(decision_text, _PM_VERDICT_PATTERNS)
    verdict = normalize_verdict(raw) or extract_recommendation(decision_text) or 'HOLD'

    pt_m = re.search(r'\*\*Price Target\*\*:\s*\$?([\d,.]+)', decision_text)
    price_target = pt_m.group(1) if pt_m else None

    th_m = re.search(r'\*\*Time Horizon\*\*:\s*([^\n]+)', decision_text)
    time_horizon = _strip_md(th_m.group(1)) if th_m else None

    reason = ''
    # 1st choice: intro paragraph before first ### sub-heading
    intro_m = re.search(r'^(.*?)(?=\n###|\Z)', decision_text, re.DOTALL)
    if intro_m:
        sents = _pm_clean_sents(intro_m.group(1))
        picked = dedupe_sentences(sents, max_n=1, prefer_numeric=True)
        if not picked:
            picked = dedupe_sentences(sents, max_n=1)
        if picked:
            reason = picked[0]

    # 2nd choice: top-level Rationale or Executive Summary heading section
    if not reason:
        for label in ('Executive Summary', 'Rationale'):
            rm = re.search(
                rf'^#{1,3}[^\n]*{label}[^\n]*\n(.*?)(?=\n#{1,3}|\Z)',
                decision_text, re.DOTALL | re.MULTILINE)
            if rm:
                sents = _pm_clean_sents(rm.group(1))
                picked = dedupe_sentences(sents, max_n=1, prefer_numeric=True)
                if not picked:
                    picked = dedupe_sentences(sents, max_n=1)
                if picked:
                    reason = picked[0]
                    break

    return {'verdict': verdict, 'price_target': price_target,
            'time_horizon': time_horizon, 'reason': reason}


def extract_rm_desk(manager_text):
    """Research Manager desk -> verdict only."""
    if not manager_text:
        return None
    m = re.search(r'\*\*Recommendation\*\*:\s*([A-Za-z ]+)', manager_text)
    raw = _strip_md(m.group(1)) if m else None
    verdict = normalize_verdict(raw) or extract_recommendation(manager_text) or 'HOLD'
    return {'verdict': verdict}


def extract_trader_desk(trader_text):
    """Trader desk -> verdict / entry price / stop loss."""
    if not trader_text:
        return None
    m = re.search(r'\*\*Action\*\*:\s*([A-Za-z ]+)', trader_text)
    raw = _strip_md(m.group(1)) if m else None
    verdict = normalize_verdict(raw) or extract_recommendation(trader_text) or 'HOLD'
    entry_m = re.search(r'\*\*Entry Price\*\*:\s*\$?([\d,.]+)', trader_text)
    stop_m = re.search(r'\*\*Stop Loss\*\*:\s*\$?([\d,.]+)', trader_text)
    return {
        'verdict': verdict,
        'entry_price': entry_m.group(1) if entry_m else None,
        'stop_loss': stop_m.group(1) if stop_m else None,
    }


def compute_composite(pm, rm, trader):
    """Majority-rule composite verdict across the three desks.

    Returns (composite, agree_names, disagree_names). Ties (no single
    majority) defer to the Portfolio Manager, who has final sign-off.
    """
    desks = [('Portfolio Manager', pm), ('Research Manager', rm), ('Trader', trader)]
    verdicts = [(name, d['verdict']) for name, d in desks if d and d.get('verdict')]
    if not verdicts:
        return 'HOLD', [], []
    counts = {}
    for _, v in verdicts:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    leaders = [v for v, c in counts.items() if c == best]
    if len(leaders) == 1:
        composite = leaders[0]
    else:
        composite = (pm['verdict'] if pm and pm.get('verdict') else verdicts[0][1])
    agree = [name for name, v in verdicts if v == composite]
    disagree = [name for name, v in verdicts if v != composite]
    return composite, agree, disagree


_KEY_METRIC_RE = re.compile(
    r'(?:'
    r'forward\s+P/E\s+(?:of\s+)?[\d.]+x?'
    r'|P/E\s+(?:of\s+)?[\d.]+x?'
    r'|PEG\s+(?:of\s+)?[\d.]+'
    r'|ROE\s+(?:of\s+)?[\d.]+%?'
    r'|gross\s+margin[s]?\s+(?:of\s+)?[\d.]+%?'
    r'|operating\s+margin[s]?\s+(?:of\s+)?[\d.]+%?'
    r'|FCF\s+(?:yield\s+)?(?:of\s+)?\$?[\d.]+'
    r'|revenue\s+growth\s+(?:of\s+)?[\d.]+%?'
    r'|EPS\s+growth\s+(?:of\s+)?[\d.]+%?'
    r')',
    re.IGNORECASE)


def _find_key_metric(text):
    """Return the first key financial metric phrase found in text, or ''."""
    if not text:
        return ''
    m = _KEY_METRIC_RE.search(text)
    return m.group(0) if m else ''


def composite_rationale_bullets(pm, rm, trader, composite, agree, disagree,
                                 decision_text='', manager_text=''):
    """3 professional bullets explaining why the composite verdict is what it is.

    Bullet 1 – PM's overall thesis (from the intro paragraph, free of
               tranche/position-sizing language).
    Bullet 2 – Desk alignment, with at least one specific metric cited.
    Bullet 3 – Investment horizon, price target, or synthesis sentence.
    """
    verdict_by_desk = {'Portfolio Manager': pm, 'Research Manager': rm, 'Trader': trader}
    out = []

    # ── Bullet 1: Best analytical rationale ──────────────────────────────────
    # Prefer the Research Manager's Rationale (cleanest analytical content),
    # then fall back to PM intro paragraph.
    bullet1_placed = False
    if manager_text:
        rat_m = re.search(r'\*\*Rationale\*\*:?\s*(.*?)(?=\n\n\*\*|\Z)',
                          manager_text, re.DOTALL)
        if rat_m:
            sents = _pm_clean_sents(rat_m.group(1))
            picked = dedupe_sentences(sents, max_n=1, prefer_numeric=True)
            if not picked:
                picked = dedupe_sentences(sents, max_n=1)
            if picked:
                out.append(picked[0])
                bullet1_placed = True
    if not bullet1_placed and pm and pm.get('reason') and not _TRADING_DETAIL_RE.search(pm['reason']):
        out.append(pm['reason'])

    # ── Bullet 2: Desk alignment with key metric ──────────────────────────────
    metric = _find_key_metric(manager_text) or _find_key_metric(decision_text)
    if disagree:
        dis_verdicts = sorted({verdict_by_desk[n]['verdict'] for n in disagree
                                if verdict_by_desk.get(n)})
        agree_str = ' and '.join(agree)
        disagree_str = ' and '.join(disagree)
        dis_v = ' / '.join(dis_verdicts)
        if metric:
            out.append(
                f"{agree_str} {'support' if len(agree) != 1 else 'supports'} {composite} "
                f"citing {metric}, while {disagree_str} "
                f"{'lean' if len(disagree) != 1 else 'leans'} toward {dis_v} "
                f"on near-term technical concerns."
            )
        else:
            out.append(
                f"{agree_str} {'support' if len(agree) != 1 else 'supports'} {composite}, "
                f"while {disagree_str} {'lean' if len(disagree) != 1 else 'leans'} "
                f"toward {dis_v}."
            )
    elif agree:
        if metric:
            out.append(
                f"All desks ({', '.join(agree)}) align on {composite}, "
                f"with {metric} as a key supporting factor."
            )
        else:
            out.append(f"All desks ({', '.join(agree)}) align on {composite}.")

    # ── Bullet 3: Time horizon / price target / synthesis ────────────────────
    if pm and pm.get('time_horizon'):
        out.append(f"Investment horizon: {pm['time_horizon']}.")
    elif pm and pm.get('price_target'):
        out.append(f"Portfolio Manager price target: ${pm['price_target']}.")
    elif len(out) < 2:
        tail = 'structural thesis outweighing near-term caution' if disagree else 'consensus across all desks'
        out.append(f"Composite verdict of {composite} reflects {tail}.")

    return out[:3]


# ── Price targets & action levels table (Page 2) ─────────────────────────────

_REASSESS_RE = re.compile(r'(?:thesis-?reassessment|reassess)[^.\n]*?\$\s*([\d,.]+)', re.IGNORECASE)


def build_price_target_rows(verdict, pm, trader, current_price, manager_text=''):
    """Build the 'Price Targets & Action Levels' table rows for the
    composite verdict (BUY / SELL / HOLD branches)."""
    rows = [['Level', 'Price', 'Notes']]
    pm = pm or {}
    trader = trader or {}
    pt = pm.get('price_target')
    entry = trader.get('entry_price')
    stop = trader.get('stop_loss')
    if not stop and manager_text:
        m = _REASSESS_RE.search(manager_text)
        if m:
            stop = m.group(1)

    if verdict == 'BUY':
        if entry:
            rows.append(['Entry', f'${entry}', 'Trader-recommended entry level'])
        if stop:
            rows.append(['Stop / Reassess', f'${stop}', 'Thesis-invalidation level'])
        if pt:
            rows.append(['Price Target', f'${pt}', 'Portfolio Manager target'])
    elif verdict == 'SELL':
        if current_price:
            rows.append(['Current Price', f'${current_price}', 'Exit at or near market'])
        if pt:
            rows.append(['Downside Target', f'${pt}', 'Portfolio Manager target'])
    else:
        if current_price:
            rows.append(['Current Price', f'${current_price}', 'No new position recommended'])
        if pt:
            rows.append(['Reassessment Level', f'${pt}', 'Portfolio Manager target'])

    if len(rows) == 1:
        rows.append(['--', 'N/A', 'No specific price levels provided in source reports'])
    return rows


# ── LLM helpers (Anthropic API; independent of the main agent's provider) ───

_anthropic_client = None
_anthropic_unavailable = False


def _get_anthropic_client():
    """Return a cached anthropic.Anthropic client, or None if unavailable."""
    global _anthropic_client, _anthropic_unavailable
    if _anthropic_client is not None:
        return _anthropic_client
    if _anthropic_unavailable:
        return None
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        _anthropic_unavailable = True
        return None
    try:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
        return _anthropic_client
    except Exception:
        _anthropic_unavailable = True
        return None


def _parse_llm_json(text):
    """Defensively parse a JSON object from an LLM response: strip ```json
    fences, then fall back to extracting the first {...} block."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


# ── Price targets & action levels (LLM-based extraction) ────────────────────

_PRICE_LEVELS_SYSTEM_PROMPT = """You extract structured price levels from an equity trading plan and research summary.

Read the provided text (a Trader's Plan and Research Manager summary) and extract:
- current_price: the latest/current price of the stock, if stated
- entry_tranches: any accumulation zones or staged entry levels (e.g. "accumulate between $700-$750" or "add on weakness below $750")
- stop_loss: the stop-loss / thesis-invalidation price level
- targets: any price targets (upside or downside), each with a short label (e.g. "Price Target", "Downside Target", "12-Month Target")
- horizon: the stated investment time horizon, if any (e.g. "6-12 months"), else null

Return ONLY a JSON object with no other text, matching exactly this schema:
{
  "current_price": float|null,
  "entry_tranches": [{"label": str, "low": float|null, "high": float|null}],
  "stop_loss": float|null,
  "targets": [{"label": str, "price": float|null}],
  "horizon": str|null
}

Do not invent values. If a field is not mentioned in the text, use null (or an empty list for entry_tranches/targets)."""


def extract_price_levels_llm(trader_text, manager_text):
    """Use claude-haiku-4-5-20251001 to extract structured price levels from
    the Trader's Plan + Research Manager text. Returns a dict matching the
    schema in _PRICE_LEVELS_SYSTEM_PROMPT, or None on any failure."""
    client = _get_anthropic_client()
    if client is None:
        return None
    combined = '\n\n'.join(t for t in (trader_text, manager_text) if t and t.strip())
    if not combined.strip():
        return None
    try:
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=600,
            system=_PRICE_LEVELS_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': combined}],
        )
        return _parse_llm_json(resp.content[0].text)
    except Exception as e:
        print(f'WARNING: price-level LLM extraction failed: {e}')
        return None


def _fmt_price(v):
    """float -> '$1,234.56', or None if v is not a usable number."""
    if v is None:
        return None
    try:
        return f'${float(v):,.2f}'
    except (TypeError, ValueError):
        return None


def _fmt_price_range(low, high):
    lo, hi = _fmt_price(low), _fmt_price(high)
    if lo and hi:
        return f'{lo} - {hi}'
    return lo or hi


def build_price_target_rows_llm(levels):
    """Build 'Price Targets & Action Levels' rows from LLM-extracted price
    levels. Returns None if levels is empty/unusable so callers can fall
    back to the regex-based build_price_target_rows."""
    if not levels:
        return None

    rows = [['Level', 'Price', 'Notes']]

    cur = _fmt_price(levels.get('current_price'))
    if cur:
        rows.append(['Current Price', cur, 'Latest close'])

    tranches = levels.get('entry_tranches') or []
    multi = len(tranches) > 1
    for i, tranche in enumerate(tranches, start=1):
        price = _fmt_price_range(tranche.get('low'), tranche.get('high'))
        if not price:
            continue
        label = tranche.get('label') or (f'Entry Tranche {i}' if multi else 'Entry Zone')
        rows.append([label, price, 'Trader-recommended accumulation level'])

    stop = _fmt_price(levels.get('stop_loss'))
    if stop:
        rows.append(['Stop Loss', stop, 'Thesis-invalidation level'])

    for target in levels.get('targets') or []:
        price = _fmt_price(target.get('price'))
        if not price:
            continue
        label = target.get('label') or 'Price Target'
        rows.append([label, price, 'Portfolio Manager / trader target'])

    if levels.get('horizon'):
        rows.append(['Horizon', '--', levels['horizon']])

    if len(rows) == 1:
        return None
    return rows


def extract_change_triggers(decision_text, manager_text):
    """2-3 'what would change the thesis' bullets from the PM/RM reports."""
    out = []
    for text in (decision_text, manager_text):
        if not text or len(out) >= 3:
            continue
        m = re.search(
            r'(?:Monitor for catalysts[^:\n]*:|Catalysts to Watch:?|\*\*Risk Management\*\*:?)'
            r'\s*(.*?)(?=\n\n\*\*|\Z)', text, re.DOTALL | re.IGNORECASE)
        if m:
            content = re.sub(r'^[\s*]+', '', m.group(1))
            for s in dedupe_sentences(extract_sentences(content), max_n=3, min_words=5):
                if s not in out:
                    out.append(s)
    return out[:3]


# ── Sentiment source table (Page 4) ──────────────────────────────────────────

_SENT_SECTION_RE = re.compile(r'#{2,4}\s*(?:\d+\.\s*)?([^\n]+)\n((?:(?!\n#{1,4}).)*)', re.DOTALL)


def _sentiment_status_count_tone(content, units):
    """(status, count, tone) for one sentiment source's content block."""
    if not content or not content.strip():
        return 'Not covered', '0', 'N/A'
    low = content.lower()
    st_m = re.search(r'\*\*Status:?\*\*\s*([^\n]+)', content, re.IGNORECASE)
    status_text = st_m.group(1).lower() if st_m else low
    if re.search(r'no (?:news|posts|messages|data)|unavailable|httperror|not found|zero (?:news|posts)',
                  status_text):
        status = 'Unavailable' if re.search(r'unavailable|httperror', status_text) else 'Not found'
    else:
        status = 'Found'

    cm = re.search(r'(\d+)\s*(?:' + '|'.join(units) + r')', content, re.IGNORECASE)
    count = cm.group(1) if cm else '0'
    if count == '0' and status == 'Found':
        n_bullets = len(re.findall(r'^\s*-\s+', content, re.MULTILINE))
        if n_bullets:
            count = str(n_bullets)

    dm = re.search(r'\*\*Direction:?\s*([^*\n]+)\*\*', content, re.IGNORECASE)
    if dm:
        d = dm.group(1).lower()
        if 'bull' in d or 'positive' in d:
            tone = 'Positive'
        elif 'bear' in d or 'negative' in d:
            tone = 'Negative'
        else:
            tone = 'Neutral'
    elif status != 'Found':
        tone = 'N/A'
    elif re.search(r'bullish|positive', low):
        tone = 'Positive'
    elif re.search(r'bearish|negative', low):
        tone = 'Negative'
    else:
        tone = 'Neutral'
    return status, count, tone


def extract_sentiment_table(text):
    """Build the Page 4 5-row source table: Yahoo Finance News, StockTwits,
    and Reddit r/WSB, r/stocks, r/investing -- regardless of whether the
    source report uses numbered '### N. Source' + Status lines, or
    unnumbered '#### Source' + Direction/prose."""
    empty = ('Not covered', '0', 'N/A')
    if not text:
        return [
            ['Source', 'Status', 'Count', 'Sentiment'],
            ['Yahoo Finance News', *empty],
            ['StockTwits', *empty],
            ['Reddit r/WSB', *empty],
            ['Reddit r/stocks', *empty],
            ['Reddit r/investing', *empty],
        ]

    m = re.search(
        r'Source-by-Source Breakdown\s*\n(.*?)'
        r'(?=\n#{1,3}\s*(?:\d+\.\s*)?(?:Cross-Source|Dominant|Catalysts|Conclusion|Summary)|\Z)',
        text, re.DOTALL | re.IGNORECASE)
    block = m.group(1) if m else text

    subsecs = [(sm.group(1).strip(), sm.group(2)) for sm in _SENT_SECTION_RE.finditer(block)]

    def find(*keywords):
        for name, content in subsecs:
            ln = name.lower()
            if any(k in ln for k in keywords):
                return content
        return ''

    rows = [['Source', 'Status', 'Count', 'Sentiment']]

    news_content = find('news', 'yahoo')
    rows.append(['Yahoo Finance News', *_sentiment_status_count_tone(news_content, ['articles?', 'headlines?'])])

    st_content = find('stocktwits')
    rows.append(['StockTwits', *_sentiment_status_count_tone(st_content, ['messages?'])])

    reddit_content = find('reddit')
    sub_specs = [
        (r'r/(?:wallstreetbets|wallstreetbels|wsb)[^\n(]*\((\d+)\s*posts?\)', 'Reddit r/WSB'),
        (r'r/stocks[^\n(]*\((\d+)\s*posts?\)', 'Reddit r/stocks'),
        (r'r/investing[^\n(]*\((\d+)\s*posts?\)', 'Reddit r/investing'),
    ]
    overall_status, overall_count, overall_tone = _sentiment_status_count_tone(
        reddit_content, ['posts?', 'messages?'])
    found_any = False
    sub_rows = []
    for pat, label in sub_specs:
        sm = re.search(pat, reddit_content, re.IGNORECASE) if reddit_content else None
        if sm:
            found_any = True
            seg = reddit_content[sm.start():sm.start() + 400]
            _, _, tone = _sentiment_status_count_tone(seg, ['posts?'])
            sub_rows.append([label, 'Found', sm.group(1), tone])
        else:
            sub_rows.append([label, overall_status, overall_count, overall_tone])
    if not found_any:
        sub_rows = [[label, overall_status, overall_count, overall_tone]
                     for _, label in sub_specs]
    rows.extend(sub_rows)
    return rows


# ── Fundamental table with Implication column (Page 4) ──────────────────────

_FUND_CATEGORY_MAP = [
    (r'p/e|p/b|peg|price.to|valuation', 'Valuation'),
    (r'revenue growth|eps growth|revenue yoy|eps yoy|^revenue\b|growth \(yoy\)', 'Growth'),
    (r'\broe\b|\broa\b|\broic\b|margin|net income|operating income|gross profit|profitab', 'Profitability'),
    (r'efficien|turnover', 'Efficiency'),
    (r'debt|leverage|liquidity|current ratio|cash|equity|book value|runway', 'Financial Health'),
    (r'market cap|^size\b', 'Size'),
]


def _categorize_metric(label):
    low = (label or '').lower()
    for pat, cat in _FUND_CATEGORY_MAP:
        if re.search(pat, low):
            return cat
    return 'Other'


_FUND_CATEGORY_TEMPLATES = {
    'Valuation': "{m} of {v} is a key input for judging whether the current price is justified by growth and profitability.",
    'Growth': "{m} of {v} indicates the pace at which the business is expanding.",
    'Profitability': "{m} of {v} reflects how efficiently the business converts revenue into profit.",
    'Efficiency': "{m} of {v} measures how effectively the company deploys its capital base.",
    'Financial Health': "{m} of {v} bears on the company's ability to meet obligations and fund continued growth.",
    'Size': "{m} of {v} sets the scale against which growth and valuation multiples are judged.",
    'Other': "{m} stands at {v}.",
}


def _generic_fund_implication(category, metric, value):
    template = _FUND_CATEGORY_TEMPLATES.get(category, _FUND_CATEGORY_TEMPLATES['Other'])
    return template.format(m=metric, v=value)


def extract_fundamental_table(text, max_rows=15):
    """Page 4 fundamentals table with a 4th 'Implication' column, handling
    the 3-col (Category|Metric|Value), FY-trend (Metric|FY..|Trend), and
    generic Metric|Value(|...|Assessment) Summary Table formats."""
    if not text:
        return []
    m = re.search(r'#{1,3}[^\n]*Summary Table[^\n]*\n+((?:\|.*\n?)+)', text, re.IGNORECASE)
    if not m:
        return []
    lines = [l for l in m.group(1).strip().split('\n') if l.strip().startswith('|')]
    rows = parse_tbl(lines)
    if len(rows) < 2:
        return []
    header = [_strip_md(h).lower() for h in rows[0]]

    out = [['Category', 'Metric', 'Value', 'Implication']]

    if 'category' in header and 'metric' in header and 'value' in header:
        ci, mi, vi = header.index('category'), header.index('metric'), header.index('value')
        for row in rows[1:]:
            if len(row) <= max(ci, mi, vi):
                continue
            cat_raw, metric, value = row[ci], row[mi], row[vi]
            category = _categorize_metric(cat_raw)
            if category == 'Other':
                category = _categorize_metric(metric)
            out.append([cat_raw, metric, value,
                         _generic_fund_implication(category, _strip_md(metric), _strip_md(value))])

    elif 'metric' in header and ('trend' in header or any(re.match(r'fy\d{4}', h) for h in header)):
        mi = header.index('metric')
        fy_idx = [i for i, h in enumerate(header) if re.match(r'fy\d{4}', h)]
        vi = fy_idx[-1] if fy_idx else (header.index('value') if 'value' in header else len(header) - 1)
        ti = header.index('trend') if 'trend' in header else None
        for row in rows[1:]:
            if len(row) <= mi:
                continue
            metric = row[mi]
            value = row[vi] if len(row) > vi else ''
            implication = _strip_md(row[ti]) if ti is not None and len(row) > ti else ''
            category = _categorize_metric(metric)
            if not implication:
                implication = _generic_fund_implication(category, _strip_md(metric), _strip_md(value))
            out.append([category, metric, value, implication])

    else:
        if 'metric' in header and 'value' in header:
            mi, vi = header.index('metric'), header.index('value')
        else:
            mi, vi = 0, min(1, len(header) - 1)
        ai = None
        for cand in ('assessment', 'implication', 'interpretation'):
            if cand in header:
                ai = header.index(cand)
                break
        for row in rows[1:]:
            if len(row) <= max(mi, vi):
                continue
            metric, value = row[mi], row[vi]
            category = _categorize_metric(metric)
            implication = _strip_md(row[ai]) if ai is not None and len(row) > ai else ''
            if not implication:
                implication = _generic_fund_implication(category, _strip_md(metric), _strip_md(value))
            out.append([category, metric, value, implication])

    if len(out) > max_rows:
        out = [out[0]] + out[1:max_rows]
    return out


# ── Technical table: normalize "Interpretation" -> "Implication" ───────────

def ensure_technical_implication(table_rows):
    """Some reports label the technical Summary Table's 4th column
    'Interpretation' instead of 'Implication'; normalize for display."""
    if not table_rows:
        return table_rows
    header = [_strip_md(h) for h in table_rows[0]]
    for i, h in enumerate(header):
        if h.lower() in ('implication', 'interpretation'):
            header[i] = 'Implication'
            break
    return [header] + table_rows[1:]


def overall_signal_from_table(table_rows):
    """Majority Bullish/Bearish/Neutral signal across a technical table's
    'Signal' column."""
    if not table_rows or len(table_rows) < 2:
        return None
    header = [_strip_md(h).lower() for h in table_rows[0]]
    if 'signal' not in header:
        return None
    si = header.index('signal')
    counts = {'Bullish': 0, 'Bearish': 0}
    for row in table_rows[1:]:
        if len(row) <= si:
            continue
        cell = _strip_md(row[si]).lower()
        if 'bullish' in cell:
            counts['Bullish'] += 1
        elif 'bearish' in cell:
            counts['Bearish'] += 1
    if counts['Bullish'] > counts['Bearish']:
        return 'Bullish'
    if counts['Bearish'] > counts['Bullish']:
        return 'Bearish'
    return 'Neutral'


# ── Investor Persona Analysis ────────────────────────────────────────────────

def extract_financial_metrics(sections):
    """Extract key financial metrics from all report sections for persona analysis."""
    fund = sections.get('fundamentals', '') or ''
    market = sections.get('market', '') or ''
    manager = sections.get('manager', '') or ''

    def _fv(text, patterns):
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    return float(re.sub(r'[,%x]', '', m.group(1)))
                except (ValueError, IndexError):
                    pass
        return None

    m = {}
    m['forward_pe'] = _fv(fund, [
        r'\|\s*\*{0,2}Forward\s+P/?E\*{0,2}\s*\|\s*([\d.]+)',
        r'forward\s+P/?E\s+of\s+([\d.]+)',
        r'Forward\s+PE[:\s*|]+([\d.]+)',
    ])
    m['ttm_pe'] = _fv(fund, [
        r'\|\s*\*{0,2}P/?E\s+Ratio[^|]*\|\s*([\d.]+)',
        r'TTM\s+P/?E[^|\d]*([\d.]+)',
        r'P/E\s+\(TTM\)[^|\d]*([\d.]+)',
    ])
    m['peg'] = _fv(fund, [
        r'\|\s*\*{0,2}PEG\s+Ratio\*{0,2}\s*\|\s*([\d.]+)',
        r'PEG\s+(?:ratio\s+)?(?:of\s+)?([\d.]+)',
    ])
    m['roe'] = _fv(fund, [
        r'\|\s*\*{0,2}Return\s+on\s+Equity[^|]*\|\s*([\d.]+)',
        r'ROE\*{0,2}\s*\|\s*([\d.]+)',
        r'([\d.]+)%\s+ROE',
        r'ROE[^\d]+([\d.]+)%',
    ])
    m['roa'] = _fv(fund, [
        r'\|\s*\*{0,2}Return\s+on\s+Assets[^|]*\|\s*([\d.]+)',
        r'ROA\*{0,2}\s*\|\s*([\d.]+)',
        r'ROA[^\d]+([\d.]+)%',
    ])
    m['gross_margin'] = _fv(fund, [
        r'\|\s*\*{0,2}Gross\s+Margin\*{0,2}\s*\|\s*([\d.]+)',
        r'([\d.]+)%\s+gross\s+margin',
        r'gross\s+margin[s]?[^\d]+([\d.]+)%',
    ])
    m['operating_margin'] = _fv(fund, [
        r'\|\s*\*{0,2}Operating\s+Margin\*{0,2}\s*\|\s*([\d.]+)',
        r'([\d.]+)%\s+operating\s+margin',
        r'operating\s+margin[^\d]+([\d.]+)%',
    ])
    m['debt_equity'] = _fv(fund, [
        r'\|\s*\*{0,2}Debt.to.Equity\*{0,2}\s*\|\s*([\d.]+)',
        r'Debt-to-Equity[^\d]+([\d.]+)',
        r'D/E[^\d]+([\d.]+)',
    ])
    m['dividend_yield'] = _fv(fund + market, [
        r'dividend\s+yield[^\d]+([\d.]+)%',
        r'yield\s+~?([\d.]+)%[^:]*dividend',
    ])
    if m['dividend_yield'] is None:
        if re.search(r'negligible|no\s+dividend|0\.0\d%', fund, re.IGNORECASE):
            m['dividend_yield'] = 0.01
    m['fcf_quarterly'] = _fv(fund + manager, [
        r'\$([\d.]+)B\s+(?:quarterly\s+)?free\s+cash\s+flow',
        r'free\s+cash\s+flow\s+of\s+\$([\d.]+)B',
        r'FCF[^\d$]*\$([\d.]+)B',
    ])
    return m


def _fmt_metric(v, suffix=''):
    if v is None:
        return 'N/A'
    if suffix == '%':
        return f'{v:.1f}%'
    if suffix == 'x':
        return f'{v:.2f}x'
    return f'{v:.2f}'


# Grouping for the Investor Persona Analysis panel -- all 13 personas render,
# organized by investment philosophy so related viewpoints sit together.
_PERSONA_GROUPS = {
    'Value Investors': ['warren_buffett', 'charlie_munger', 'ben_graham',
                         'mohnish_pabrai', 'aswath_damodaran'],
    'Growth Investors': ['cathie_wood', 'peter_lynch', 'phil_fisher',
                          'rakesh_jhunjhunwala'],
    'Macro & Trading': ['stanley_druckenmiller', 'michael_burry', 'nassim_taleb'],
    'Other': ['bill_ackman'],
}


def select_personas():
    """Return all 13 persona filename stems, grouped by investment philosophy."""
    return _PERSONA_GROUPS


_PERSONA_DISPLAY_NAMES = {
    'warren_buffett':      'Warren Buffett',
    'charlie_munger':      'Charlie Munger',
    'cathie_wood':         'Cathie Wood',
    'stanley_druckenmiller': 'Stan Druckenmiller',
    'peter_lynch':         'Peter Lynch',
    'michael_burry':       'Michael Burry',
    'aswath_damodaran':    'Aswath Damodaran',
    'ben_graham':          'Benjamin Graham',
    'mohnish_pabrai':      'Mohnish Pabrai',
    'nassim_taleb':        'Nassim Taleb',
    'phil_fisher':         'Phil Fisher',
    'rakesh_jhunjhunwala': 'Rakesh Jhunjhunwala',
    'bill_ackman':         'Bill Ackman',
}


_PERSONA_SYSTEM_SUFFIX = (
    "\n\nApply this investor's documented philosophy STRICTLY, including their known aversions. "
    "SELL and AVOID are valid and expected verdicts. Cheap P/E on peak cyclical earnings is a "
    "classic value trap, not automatically a buy. Do not anchor on the desk verdicts. Unanimous "
    "agreement across philosophically opposed investors on a cyclical stock indicates a "
    "reasoning failure."
)


def _load_persona_system_prompt(persona_name, persona_dir):
    """Load a persona's '## System Prompt' code block plus its
    '## Hard Rules / Known Aversions' section, with the anti-sycophancy
    suffix appended. Returns None if the file or system prompt is missing."""
    path = persona_dir / f'{persona_name}.md'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None

    sp_m = re.search(r'## System Prompt\s*\n```\s*\n(.*?)\n```', text, re.DOTALL)
    if not sp_m:
        return None
    system_prompt = sp_m.group(1).strip()

    hr_m = re.search(r'## Hard Rules / Known Aversions\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    if hr_m:
        system_prompt += '\n\n## Hard Rules / Known Aversions\n' + hr_m.group(1).strip()

    return system_prompt + _PERSONA_SYSTEM_SUFFIX


def _build_persona_context(ticker, metrics, bull_view, bear_view, key_risks_table, composite):
    """Build the shared user-message context fed to every persona LLM call:
    headline metrics, bull case, bear case, and key risks -- so personas
    can't just parrot the bullish headline numbers."""
    lines = [f'Ticker: {ticker}', '', 'Financial Metrics:']
    labels = {
        'forward_pe': ('Forward P/E', 'x'), 'ttm_pe': ('TTM P/E', 'x'),
        'peg': ('PEG Ratio', ''), 'roe': ('ROE', '%'), 'roa': ('ROA', '%'),
        'gross_margin': ('Gross Margin', '%'), 'operating_margin': ('Operating Margin', '%'),
        'debt_equity': ('Debt-to-Equity', ''), 'dividend_yield': ('Dividend Yield', '%'),
        'fcf_quarterly': ('Quarterly FCF ($B)', ''),
    }
    for key, (label, suffix) in labels.items():
        val = metrics.get(key)
        if val is not None:
            lines.append(f'- {label}: {_fmt_metric(val, suffix)}')

    if bull_view and bull_view.get('bullets'):
        lines.append('\nBull Case (from the research team):')
        lines.extend(f'- {b}' for b in bull_view['bullets'])

    if bear_view and bear_view.get('bullets'):
        lines.append('\nBear Case / Key Risks (from the research team):')
        lines.extend(f'- {b}' for b in bear_view['bullets'])

    if key_risks_table and len(key_risks_table) > 1:
        lines.append('\nKey Risks (from fundamentals analysis):')
        for row in key_risks_table[1:]:
            factor, severity, evidence = (row + ['', '', ''])[:3]
            lines.append(f'- {factor} (Severity: {severity}): {evidence}')

    if composite:
        lines.append(f'\nDesk Composite Verdict (context only -- do not anchor on this): {composite}')

    return '\n'.join(lines)


_PERSONA_VERDICT_JSON_INSTRUCTIONS = (
    '\n\nRespond with ONLY a JSON object, no other text, in this exact schema:\n'
    '{"verdict": "BUY|HOLD|SELL|AVOID", '
    '"focus_metric": "<short string, e.g. \'ROE 18% | Fwd P/E 8x\'>", '
    '"rationale": "<2-4 sentence rationale in your voice, citing specific numbers>"}'
)


def _persona_analysis_llm(persona_name, system_prompt, context_text, ticker):
    """Call claude-sonnet-4-6 with a persona's system prompt + the shared
    context. Returns (verdict, focus, rationale) or None on any failure."""
    client = _get_anthropic_client()
    if client is None or not system_prompt:
        return None
    try:
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=700,
            system=system_prompt,
            messages=[{'role': 'user', 'content': context_text + _PERSONA_VERDICT_JSON_INSTRUCTIONS}],
        )
        data = _parse_llm_json(resp.content[0].text)
        if not data:
            return None
        verdict = str(data.get('verdict', '')).upper().strip()
        if verdict not in ('BUY', 'HOLD', 'SELL', 'AVOID'):
            return None
        rationale = str(data.get('rationale', '')).strip()
        if not rationale:
            return None
        focus = str(data.get('focus_metric', '')).strip() or 'See rationale'
        return verdict, focus, rationale
    except Exception as e:
        print(f'WARNING: persona LLM call failed for {persona_name} ({ticker}): {e}')
        return None


def _persona_fallback(persona_name, ticker):
    """Used when the LLM call is unavailable or fails -- a neutral
    placeholder rather than a fabricated rule-based verdict."""
    display = _PERSONA_DISPLAY_NAMES.get(persona_name, persona_name.replace('_', ' ').title())
    return ('HOLD', 'N/A',
            f"{display}'s analysis could not be generated (LLM call unavailable or failed). "
            f"See the Fundamental and Technical Analysis sections for {ticker}'s underlying metrics.")


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

    footer_ts = _hkt_timestamp()

    def draw_footer(canvas, doc):
        canvas.saveState()
        w, _ = letter
        canvas.setStrokeColor(MGRAY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75*inch, 0.65*inch, w - 0.75*inch, 0.65*inch)
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75*inch, 0.45*inch,
                          f'{ticker}  |  {footer_ts}  |  Kevin Cheng Investment Research')
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
        # Fall back to an even split summing to the usable frame width
        # (7.0in for letter with 0.75in margins) if col_widths doesn't
        # match the actual column count -- avoids overflow/misalignment.
        if col_widths and len(col_widths) == ncols:
            cw = col_widths
        else:
            cw = [(7.0*inch)/ncols]*ncols
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

    def verdict_chip(label, vd, width=1.7*inch, display=None):
        fg_hex, bg_hex = verdict_hex(vd)
        text = display if display is not None else vd
        chip_st = ps('d_chip', fontSize=12, fontName='Helvetica-Bold',
                      textColor=colors.HexColor(fg_hex), leading=15, alignment=TA_CENTER)
        t = Table([[Paragraph(f'{label}: <b>{text}</b>', chip_st)]], colWidths=[width])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0),(-1,-1), colors.HexColor(bg_hex)),
            ('BOX', (0,0),(-1,-1), 0.5, colors.HexColor(fg_hex)),
            ('TOPPADDING', (0,0),(-1,-1), 6),
            ('BOTTOMPADDING', (0,0),(-1,-1), 6),
        ]))
        return t

    def desk_verdict_table(pm_v, rm_v, trader_v, composite_v):
        headers = ['Portfolio Manager', 'Research Manager', 'Trader', 'Composite']
        verdicts = [pm_v, rm_v, trader_v, composite_v]
        header_row = [Paragraph(h, tbl_h) for h in headers]
        cell_row = []
        style = [
            ('BACKGROUND', (0,0),(-1,0), DGRAY),
            ('GRID', (0,0),(-1,-1), 0.4, MGRAY),
            ('TOPPADDING', (0,0),(-1,-1), 6),
            ('BOTTOMPADDING', (0,0),(-1,-1), 6),
            ('VALIGN', (0,0),(-1,-1), 'MIDDLE'),
            ('LINEABOVE', (3,1),(3,1), 1.5, NAVY),
        ]
        for i, vd in enumerate(verdicts):
            fg_hex, bg_hex = verdict_hex(vd)
            cell_st = ps(f'd_dvc{i}', fontSize=12, fontName='Helvetica-Bold',
                         textColor=colors.HexColor(fg_hex), leading=15, alignment=TA_CENTER)
            cell_row.append(Paragraph(vd, cell_st))
            style.append(('BACKGROUND', (i,1),(i,1), colors.HexColor(bg_hex)))
        t = Table([header_row, cell_row], colWidths=[1.75*inch]*4)
        t.setStyle(TableStyle(style))
        return t

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
    key_risks_table = extract_key_risks_table(sections['fundamentals'])

    bull_view = extract_research_view(sections['bull'], 'Bull') if sections['bull'] else None
    bear_view = extract_research_view(sections['bear'], 'Bear') if sections['bear'] else None
    mgr_summary = extract_manager_summary(sections['manager']) if sections['manager'] else None

    tech = extract_technical(sections['market']) if sections['market'] else None
    fund = extract_fundamentals_summary(sections['fundamentals']) if sections['fundamentals'] else None
    sent = extract_sentiment_summary(sections['sentiment']) if sections['sentiment'] else None
    news = extract_news_summary(sections['news']) if sections['news'] else None
    trader = extract_trader_plan(sections['trader']) if sections['trader'] else None

    # ── Investment Dashboard data: latest price, exchange/sector, desks ──────
    latest_price, price_date = extract_latest_price(sections['market'], ticker)
    exch_sector = extract_exchange_sector(sections['fundamentals'], sections['market'])
    pm_desk = extract_pm_desk(sections['decision'])
    rm_desk = extract_rm_desk(sections['manager'])
    trader_desk = extract_trader_desk(sections['trader'])
    composite, agree_desks, disagree_desks = compute_composite(pm_desk, rm_desk, trader_desk)
    price_levels = extract_price_levels_llm(sections.get('trader', ''), sections.get('manager', ''))
    price_target_rows = build_price_target_rows_llm(price_levels)
    if not price_target_rows:
        price_target_rows = build_price_target_rows(
            composite, pm_desk, trader_desk, latest_price, sections['manager'])
    rationale_bullets = composite_rationale_bullets(
        pm_desk, rm_desk, trader_desk, composite, agree_desks, disagree_desks,
        decision_text=sections['decision'], manager_text=sections['manager'])
    change_triggers = extract_change_triggers(sections['decision'], sections['manager'])

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
        Paragraph(f'Analysis Date: {date}  |  Report Generated: {footer_ts}',
                  ps('d_cd', fontSize=13, textColor=GRAY, leading=17, alignment=TA_CENTER)),
    ]
    if latest_price and price_date:
        try:
            d = datetime.strptime(price_date, '%Y-%m-%d')
            price_line = f'Last Close ({d.strftime("%b")} {d.day}): ${latest_price}'
        except ValueError:
            price_line = f'Last Close: ${latest_price} (as of {price_date})'
    elif latest_price:
        price_line = f'Latest Price: ${latest_price}'
    else:
        price_line = 'Latest Price: N/A -- see Technical Analysis section'
    story.append(Paragraph(esc(price_line),
              ps('d_cpx', fontSize=12, fontName='Helvetica-Bold',
                 textColor=NAVY, leading=16, alignment=TA_CENTER)))
    exch_bits = [v for v in (exch_sector['exchange'], exch_sector['sector'], exch_sector['industry']) if v]
    if exch_bits:
        story.append(Paragraph(esc(' | '.join(exch_bits)),
                  ps('d_cex', fontSize=10, textColor=GRAY, leading=13, alignment=TA_CENTER)))
    story += [
        Spacer(1, 0.85*inch),
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

    # ── PAGE 2: Investment Dashboard ──────────────────────────────────────
    story += section_header('Investment Dashboard')

    story.append(Paragraph('Desk Verdicts', h2s))
    story += [desk_verdict_table(pm_desk['verdict'] if pm_desk else 'HOLD',
                                  rm_desk['verdict'] if rm_desk else 'HOLD',
                                  trader_desk['verdict'] if trader_desk else 'HOLD',
                                  composite),
              Spacer(1, 10)]

    if rationale_bullets:
        story.append(Paragraph('Composite Rationale', h2s))
        story += bullets(rationale_bullets)

    story.append(Paragraph('Price Targets & Action Levels', h2s))
    story += rl_table(price_target_rows, col_widths=[1.8*inch, 1.2*inch, 4.0*inch])

    if verdict['actions']:
        story.append(Paragraph('Strategic Actions', h2s))
        story += bullets(verdict['actions'])

    story.append(Spacer(1, 8))
    story.append(Paragraph('Key Risks', h2s))
    if key_risks_table:
        story += rl_table(key_risks_table,
                           col_widths=[1.5*inch, 0.8*inch, 2.6*inch, 2.1*inch])
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
        if bull_view['bullets']:
            story += bullets(bull_view['bullets'])

    if bear_view:
        story.append(Paragraph('Bear Case', h2s))
        if bear_view['bullets']:
            story += bullets(bear_view['bullets'])

    if mgr_summary:
        story.append(Paragraph('Research Manager Summary', h2s))
        rm_v = rm_desk['verdict'] if rm_desk else 'HOLD'
        story += [verdict_chip('Research Manager', rm_v, width=2.5*inch), Spacer(1, 6)]
        if mgr_summary['intro']:
            story.append(Paragraph(md2rl(' '.join(mgr_summary['intro'])), body))
        if mgr_summary['swing']:
            story.append(Paragraph('Key Swing Factors', h3s))
            story += bullets(mgr_summary['swing'])

    story.append(PageBreak())

    # ── PAGE 4: Analyst Reports Summary ───────────────────────────────────
    story += section_header('Analyst Reports Summary')

    _SIGNAL_TO_VERDICT = {'Bullish': 'BUY', 'Bearish': 'SELL', 'Neutral': 'HOLD'}

    story.append(Paragraph('Technical Analysis', h2s))
    if tech and tech['available']:
        if tech['intro']:
            story.append(Paragraph(md2rl(' '.join(tech['intro'])), body))
        if tech['table']:
            story += rl_table(ensure_technical_implication(tech['table']))
            overall_tech = overall_signal_from_table(tech['table'])
            if overall_tech:
                story += [verdict_chip('Technical Signal', _SIGNAL_TO_VERDICT[overall_tech],
                                        width=3.0*inch, display=overall_tech),
                          Spacer(1, 6)]
        if tech['recommendation']:
            story.append(Paragraph(f"Technical Signal (Analyst): <b>{tech['recommendation']}</b>", body))
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
        fund_table = extract_fundamental_table(sections['fundamentals'])
        if fund_table:
            story += rl_table(fund_table,
                               col_widths=[1.3*inch, 1.6*inch, 1.3*inch, 2.8*inch])
        elif fund['table']:
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
        sent_table = extract_sentiment_table(sections['sentiment'])
        if sent_table:
            story += rl_table(sent_table, col_widths=[2.0*inch, 1.3*inch, 1.0*inch, 2.7*inch])
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
    if news and (news['themes'] or news['table'] or news.get('actionable')):
        for title, sent_text in news['themes']:
            story.append(Paragraph(esc(title), h3s))
            story.append(Paragraph(md2rl(sent_text), body))
        if news.get('actionable'):
            story.append(Paragraph('Actionable Insights', h3s))
            story += rl_table(news['actionable'],
                              col_widths=[2.1*inch, 1.0*inch, 0.85*inch, 3.05*inch])
        if news['table']:
            story.append(Paragraph('Key Data Points', h3s))
            story += rl_table(news['table'],
                              col_widths=[1.4*inch, 3.15*inch, 1.4*inch, 1.05*inch])
        if news.get('bullish'):
            story.append(Paragraph('Key Bullish Catalysts', h3s))
            story += rl_table(news['bullish'],
                              col_widths=[1.75*inch, 3.5*inch, 1.75*inch])
        if news.get('bearish'):
            story.append(Paragraph('Key Bearish Risks', h3s))
            story += rl_table(news['bearish'],
                              col_widths=[1.75*inch, 3.5*inch, 1.75*inch])
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
        trader_v = trader_desk['verdict'] if trader_desk else 'HOLD'
        story += [verdict_chip('Trader Action', trader_v, width=2.5*inch), Spacer(1, 6)]
        if trader['action']:
            story.append(Paragraph(f"Action: <b>{esc(trader['action'])}</b>", body))
        if pm_desk and pm_desk.get('time_horizon'):
            story.append(Paragraph(f"Investment Horizon: <b>{esc(pm_desk['time_horizon'])}</b>", body))
        if trader['reasoning']:
            story.append(Paragraph('Reasoning', h3s))
            story.append(Paragraph(md2rl(' '.join(trader['reasoning'])), body))
        if trader['sizing']:
            story.append(Paragraph('Position Sizing', h3s))
            story.append(Paragraph(md2rl(trader['sizing']), body))
        if change_triggers:
            story.append(Paragraph('Reassessment Triggers', h3s))
            story += bullets(change_triggers)
        if trader['final']:
            story.append(Spacer(1, 8))
            story.append(Paragraph(f"FINAL TRANSACTION PROPOSAL: <b>{trader['final']}</b>", h2s))
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            'See Investment Dashboard (page 2) for price targets, stop-loss levels, '
            'and the composite desk verdict.', small))

    # ── LAST PAGE(S): Investor Persona Analysis ───────────────────────────────
    persona_dir = base / 'kevin_personas'
    if persona_dir.exists():
        story.append(PageBreak())
        story += section_header('Investor Persona Analysis')
        story.append(Paragraph(
            'How legendary investors would approach this stock based on their investment philosophy',
            ps('d_psubtitle', fontSize=10, textColor=GRAY, leading=13, spaceAfter=10)))

        fin_metrics = extract_financial_metrics(sections)
        persona_groups = select_personas()
        persona_context = _build_persona_context(
            ticker, fin_metrics, bull_view, bear_view, key_risks_table, composite)

        DARK_NAVY = colors.HexColor('#2C3E50')
        BUY_BG    = colors.HexColor('#1B7E45')
        HOLD_BG   = colors.HexColor('#B8860B')
        SELL_BG   = colors.HexColor('#B22222')

        def _verd_bg(v):
            if v == 'BUY':
                return BUY_BG
            if v in ('SELL', 'AVOID'):
                return SELL_BG
            return HOLD_BG

        AVAIL_W = 7.0 * inch
        pcol = [AVAIL_W*0.18, AVAIL_W*0.12, AVAIL_W*0.22, AVAIL_W*0.48]

        p_hdr_st  = ps('d_phdr',  fontSize=8, fontName='Helvetica-Bold',
                        textColor=colors.white, leading=10, alignment=TA_CENTER)
        p_lbl_st  = ps('d_plbl',  fontSize=8, fontName='Helvetica-Bold',
                        textColor=NAVY, leading=10)
        p_body_st = ps('d_pbdy',  fontSize=7.5, leading=10, spaceAfter=0,
                        textColor=colors.HexColor('#212529'))

        hdr_row = [Paragraph(h, p_hdr_st)
                   for h in ('Investor', 'Verdict', 'Focus Metric', 'Rationale')]

        all_verdicts = []

        for group_name, pnames in persona_groups.items():
            story.append(Paragraph(group_name, h2s))

            tdata  = [hdr_row]
            tstyle = [
                ('BACKGROUND',    (0,0),(-1,0),  DARK_NAVY),
                ('GRID',          (0,0),(-1,-1), 0.5, colors.HexColor('#CCCCCC')),
                ('TOPPADDING',    (0,0),(-1,-1), 6),
                ('BOTTOMPADDING', (0,0),(-1,-1), 6),
                ('LEFTPADDING',   (0,0),(-1,-1), 8),
                ('RIGHTPADDING',  (0,0),(-1,-1), 8),
                ('VALIGN',        (0,0),(-1,-1), 'TOP'),
            ]

            for ri, pname in enumerate(pnames, start=1):
                system_prompt = _load_persona_system_prompt(pname, persona_dir)
                result = _persona_analysis_llm(pname, system_prompt, persona_context, ticker)
                if result is None:
                    result = _persona_fallback(pname, ticker)
                verdict, focus, rationale = result
                all_verdicts.append(verdict)

                bg = _verd_bg(verdict)
                display = _PERSONA_DISPLAY_NAMES.get(pname, pname.replace('_', ' ').title())
                row_bg  = colors.white if ri % 2 == 1 else colors.HexColor('#F8F8F8')

                v_st = ps(f'd_pv_{group_name}_{ri}', fontSize=9, fontName='Helvetica-Bold',
                          leading=11, alignment=TA_CENTER, textColor=colors.white)
                tdata.append([
                    Paragraph(clean(display), p_lbl_st),
                    Paragraph(verdict, v_st),
                    Paragraph(clean(focus), p_body_st),
                    Paragraph(md2rl(clean(rationale)), p_body_st),
                ])
                tstyle.append(('BACKGROUND', (1, ri), (1, ri), bg))
                tstyle.append(('BACKGROUND', (0, ri), (0, ri), row_bg))
                tstyle.append(('BACKGROUND', (2, ri), (3, ri), row_bg))

            persona_tbl = Table(tdata, colWidths=pcol, repeatRows=1)
            persona_tbl.setStyle(TableStyle(tstyle))
            story += [Spacer(1, 6), persona_tbl, Spacer(1, 10)]

        story.append(Paragraph(
            "Note: Persona verdicts apply each investor's documented philosophy and known "
            "aversions to the bull case, bear case, and key risks for this report. "
            "Rationales are illustrative applications of their published frameworks, "
            "not predictive statements.",
            ps('d_pdiscl', fontSize=7, textColor=GRAY, leading=9)))

        if all_verdicts:
            counts = {}
            for v in all_verdicts:
                counts[v] = counts.get(v, 0) + 1
            top_verdict, top_count = max(counts.items(), key=lambda kv: kv[1])
            if top_count >= 10:
                print(f'PERSONA HOMOGENEITY WARNING: {top_count}/{len(all_verdicts)} '
                      f'personas returned {top_verdict} for {ticker}')

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
