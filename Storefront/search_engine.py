"""Intelligent catalog search for the storefront.

Replaces the old "every token must be a LIKE substring" search with:

  1. Query deconstruction — lowercase, strip noise, pull out size/units
     (1kg, 250ml) and normalize them to how the catalog stores sizes, and
     expand synonyms / local terms (toyo<->soy sauce, gatas<->milk, coke<->
     coca cola, brand nicknames).
  2. Ranked retrieval — an FTS5 index (porter stemmer) weighted name > brand >
     category, with prefix matching, ordered by bm25 relevance (sales as a
     tiebreaker handled by the caller).
  3. Graceful degradation — strict all-tokens match, then any-token match,
     then a typo-tolerant pass that corrects tokens against a vocabulary of
     real brands/keywords ("zonrocks"->"zonrox"), so the customer never hits a
     bare "no results". Surfaces a "did you mean" correction and suggested
     brands/categories.

Everything runs on SQLite FTS5 (porter + trigram) already compiled into the
Pi's sqlite3 — no external services or model downloads.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass, field

# --- bm25 column weights (higher = more important; bm25 lower score = better) ---
# Columns, in this order, must match the CREATE VIRTUAL TABLE below.
_FTS_WEIGHTS = (10.0, 8.0, 2.0, 4.0, 3.0)  # name, brand, size, category, keywords

# How many ranked candidates we hand back to the caller (which then applies
# department/price filters + pagination). Generous so filters still have a pool.
_CANDIDATE_LIMIT = 600

# Below this many strict-match hits we widen the search (OR, then fuzzy).
_THIN_RESULTS = 3


# ---------------------------------------------------------------------------
# Query deconstruction
# ---------------------------------------------------------------------------

# Seed synonym / local-term map. Each key expands to itself + listed terms at
# QUERY time (so "soy sauce" also looks for "toyo"). Phase 3 grows this from a
# curated CSV (search_synonyms.csv) loaded by load_synonyms(); this inline seed
# keeps search useful out of the box.
_SEED_SYNONYMS: dict[str, list[str]] = {
    # Filipino <-> English staples
    'soy': ['toyo'], 'sauce': [], 'toyo': ['soy'],
    'vinegar': ['suka'], 'suka': ['vinegar'],
    'milk': ['gatas'], 'gatas': ['milk'],
    'rice': ['bigas'], 'bigas': ['rice'],
    'salt': ['asin'], 'asin': ['salt'],
    'sugar': ['asukal'], 'asukal': ['sugar'],
    'soap': ['sabon'], 'sabon': ['soap'],
    'oil': ['mantika'], 'mantika': ['oil'],
    'bread': ['tinapay'], 'tinapay': ['bread'],
    'egg': ['itlog'], 'itlog': ['egg'],
    'water': ['tubig'], 'tubig': ['water'],
    'coffee': ['kape'], 'kape': ['coffee'],
    'garlic': ['bawang'], 'bawang': ['garlic'],
    'onion': ['sibuyas'], 'sibuyas': ['onion'],
    'chicken': ['manok'], 'manok': ['chicken'],
    'fish': ['isda'], 'isda': ['fish'],
    'pork': ['baboy'], 'baboy': ['pork'],
    # Brand nicknames / common shorthand
    'coke': ['coca', 'cola'], 'sprite': [], 'royal': [],
    'maggi': ['maggie'], 'lucky': ['lucky me'], 'nido': [],
    # Category shorthand
    'dishwashing': ['dishwash'], 'detergent': ['powder', 'laundry'],
    'toothpaste': ['colgate'], 'diaper': ['diapers'], 'napkin': ['napkins'],
    'juice': ['drink'], 'noodles': ['noodle', 'pancit'], 'pancit': ['noodles'],
    # Same-product / alternate spellings
    'ketchup': ['catsup'], 'catsup': ['ketchup'],
    'tomato': ['tomatoes'],
    'cookies': ['biscuit', 'biscuits'], 'biscuit': ['cookies', 'biscuits'],
    'candy': ['candies'], 'tissue': ['tissues'],
    'sardines': ['sardinas'], 'sardinas': ['sardines'],
    'tuna': ['atun'], 'corned': ['corn'], 'mongo': ['monggo'], 'monggo': ['mongo'],
}

# Stopwords that add no retrieval value (kept tiny — we don't want to drop real
# product words). Removed only when other tokens remain.
_STOPWORDS = {'the', 'a', 'an', 'of', 'for', 'and', 'with', 'in', 'sa', 'ng', 'na', 'mga'}

# size/unit patterns -> normalized forms the catalog uses (e.g. "1kg" stored as
# "1k", grams kept as-is). We emit BOTH the spoken and stored forms so either
# matches.
_UNIT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(kg|kilo|kilos|g|gram|grams|gm|ml|l|li|liter|litre|liters|pcs|pc|pieces|s)\b')

_TOKEN_RE = re.compile(r'[a-z0-9]+')


def _normalize_units(text: str) -> tuple[str, list[str]]:
    """Pull size/unit mentions out and return (cleaned_text, extra_size_terms).

    "1kg" -> extra terms ["1kg", "1k", "1000g"]; "250ml" -> ["250ml", "250"].
    The original text keeps the tokens too (so "1kg" still matches a literal
    "1kg" name), but the extra forms bridge to how sizes are actually stored.
    """
    extras: list[str] = []

    def _expand(m: re.Match) -> str:
        num, unit = m.group(1), m.group(2)
        unit = unit.lower()
        try:
            val = float(num)
        except ValueError:
            return m.group(0)
        n_int = int(val) if val.is_integer() else val
        if unit in ('kg', 'kilo', 'kilos'):
            extras.extend([f'{n_int}kg', f'{n_int}k'])
            if val.is_integer():
                extras.append(f'{int(val*1000)}g')
        elif unit in ('g', 'gram', 'grams', 'gm'):
            extras.append(f'{n_int}g')
        elif unit in ('ml',):
            extras.append(f'{n_int}ml')
        elif unit in ('l', 'li', 'liter', 'litre', 'liters'):
            extras.extend([f'{n_int}l', f'{n_int}li'])
        elif unit in ('pcs', 'pc', 'pieces', 's'):
            extras.extend([f'{n_int}s', f'{n_int}pcs'])
        return f'{num} {unit}'

    cleaned = _UNIT_RE.sub(_expand, text)
    return cleaned, extras


@dataclass
class ParsedQuery:
    raw: str
    groups: list[list[str]]          # all groups; each is a set of OR-alternatives (synonyms)
    core_groups: list[list[str]]     # word groups only (drives strict AND)
    flat_tokens: list[str]           # de-duplicated primary tokens (for fuzzy/vocab)
    is_barcode: bool = False
    barcode: str = ''


def parse_query(raw: str, synonyms: dict[str, list[str]] | None = None) -> ParsedQuery:
    syn = synonyms or _SEED_SYNONYMS
    text = (raw or '').strip().lower()

    digits = re.sub(r'\D', '', text)
    if digits and len(digits) >= 4 and len(re.sub(r'[\s\d]', '', text)) == 0:
        # Pure numeric query — treat as barcode / PLU lookup.
        return ParsedQuery(raw=raw, groups=[], core_groups=[], flat_tokens=[digits],
                           is_barcode=True, barcode=digits)

    cleaned, size_extras = _normalize_units(text)
    tokens = [t for t in _TOKEN_RE.findall(cleaned) if t]
    # Drop stopwords only if meaningful tokens remain.
    meaningful = [t for t in tokens if t not in _STOPWORDS]
    if meaningful:
        tokens = meaningful

    groups: list[list[str]] = []
    seen_group_keys: set[str] = set()
    for tok in tokens:
        alts = [tok]
        for extra in syn.get(tok, []):
            alts.extend(extra.split())
        # de-dup while preserving order
        alts = list(dict.fromkeys(a for a in alts if len(a) >= 2 or a.isdigit()))
        if not alts:
            continue
        key = '|'.join(sorted(alts))
        if key in seen_group_keys:
            continue
        seen_group_keys.add(key)
        groups.append(alts)

    # "core" groups are real words (drive the strict AND). Pure-digit / very
    # short groups (sizes, counts) only widen/boost — requiring them in the AND
    # would wrongly drop results (e.g. "1.5l coke" must still find Coke).
    core_groups = [g for g in groups if not g[0].isdigit() and len(g[0]) >= 2]

    for extra in size_extras:
        key = extra
        if key not in seen_group_keys:
            seen_group_keys.add(key)
            groups.append([extra])

    flat = list(dict.fromkeys(t for g in groups for t in g))
    return ParsedQuery(raw=raw, groups=groups, core_groups=core_groups, flat_tokens=flat)


def _fts_match_expr(groups: list[list[str]], conjunction: str) -> str:
    """Build an FTS5 MATCH string. conjunction is 'AND' or 'OR' across groups;
    within a group the synonym alternatives are always OR'd. Prefix '*' lets
    'choc' hit 'chocolate'."""
    parts = []
    for alts in groups:
        ors = ' OR '.join(f'{a}*' for a in alts)
        parts.append(f'({ors})')
    if not parts:
        return ''
    joiner = f' {conjunction} '
    return joiner.join(parts)


# ---------------------------------------------------------------------------
# Index build (idempotent, signature-gated)
# ---------------------------------------------------------------------------

def _catalog_signature(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(LENGTH(search_text)), 0) AS s FROM products"
    ).fetchone()
    return f"{row['c']}:{row['s']}"


def _index_is_fresh(conn: sqlite3.Connection) -> bool:
    have = {r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name IN ('products_fts','search_vocab','search_meta')"
    ).fetchall()}
    if not {'products_fts', 'search_vocab', 'search_meta'} <= have:
        return False
    try:
        stored = conn.execute("SELECT v FROM search_meta WHERE k='signature'").fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(stored) and stored['v'] == _catalog_signature(conn)


def ensure_search_index(conn: sqlite3.Connection, force: bool = False) -> bool:
    """Create/refresh the FTS index + vocabulary if the catalog changed.

    Cheap when already fresh (one COUNT/SUM). Returns True if it (re)built.
    Safe to call before every search and from the publish script.
    """
    if not force and _index_is_fresh(conn):
        return False

    conn.execute("DROP TABLE IF EXISTS products_fts")
    conn.execute("DROP TABLE IF EXISTS search_vocab")
    conn.execute("CREATE TABLE IF NOT EXISTS search_meta (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute(
        """
        CREATE VIRTUAL TABLE products_fts USING fts5(
            merkey UNINDEXED,
            name, brand, size, category, keywords,
            tokenize = 'porter unicode61 remove_diacritics 1'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO products_fts (merkey, name, brand, size, category, keywords)
        SELECT merkey,
               COALESCE(name, ''),
               COALESCE(brand, ''),
               COALESCE(size, ''),
               COALESCE(category_name, '') || ' ' || COALESCE(department_name, ''),
               COALESCE(supplier_name, '') || ' ' ||
               COALESCE(class_l1_name, '') || ' ' || COALESCE(class_l2_name, '') || ' ' ||
               COALESCE(class_l3_name, '')
        FROM products
        WHERE active = 1
        """
    )

    # Vocabulary for typo correction: distinct brands + frequent name tokens.
    conn.execute("CREATE TABLE search_vocab (term TEXT PRIMARY KEY, kind TEXT, freq INTEGER)")
    conn.execute(
        """
        INSERT OR IGNORE INTO search_vocab (term, kind, freq)
        SELECT LOWER(TRIM(brand)), 'brand', COUNT(*)
        FROM products WHERE active = 1 AND brand IS NOT NULL AND TRIM(brand) <> ''
        GROUP BY LOWER(TRIM(brand))
        """
    )
    # name tokens (length >= 3) harvested in Python — SQLite has no tokenizer
    # accessible here for arbitrary splitting.
    freq: dict[str, int] = {}
    for (name,) in conn.execute("SELECT name FROM products WHERE active = 1 AND name IS NOT NULL"):
        for tok in _TOKEN_RE.findall(name.lower()):
            if len(tok) >= 3 and not tok.isdigit():
                freq[tok] = freq.get(tok, 0) + 1
    conn.executemany(
        "INSERT OR IGNORE INTO search_vocab (term, kind, freq) VALUES (?, 'word', ?)",
        [(t, f) for t, f in freq.items() if f >= 2],
    )

    conn.execute(
        "INSERT OR REPLACE INTO search_meta (k, v) VALUES ('signature', ?)",
        (_catalog_signature(conn),),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Vocabulary / fuzzy correction
# ---------------------------------------------------------------------------

_vocab_cache: dict[str, list[str]] = {}


def _vocab(conn: sqlite3.Connection) -> list[str]:
    sig_row = conn.execute("SELECT v FROM search_meta WHERE k='signature'").fetchone()
    sig = sig_row['v'] if sig_row else ''
    cached = _vocab_cache.get(sig)
    if cached is None:
        cached = [r['term'] for r in conn.execute(
            "SELECT term FROM search_vocab ORDER BY freq DESC"
        ).fetchall()]
        _vocab_cache.clear()
        _vocab_cache[sig] = cached
    return cached


def correct_tokens(conn: sqlite3.Connection, tokens: list[str]) -> tuple[list[str], bool]:
    """Map typo'd tokens to the closest real vocabulary term. Returns
    (corrected_tokens, changed?)."""
    vocab = _vocab(conn)
    if not vocab:
        return tokens, False
    out: list[str] = []
    changed = False
    for tok in tokens:
        if tok.isdigit() or len(tok) < 3:
            out.append(tok)
            continue
        # already a real term?
        if tok in vocab:
            out.append(tok)
            continue
        match = difflib.get_close_matches(tok, vocab, n=1, cutoff=0.84)
        if match and match[0] != tok:
            out.append(match[0])
            changed = True
        else:
            out.append(tok)
    return out, changed


# ---------------------------------------------------------------------------
# Search execution
# ---------------------------------------------------------------------------

@dataclass
class SearchOutcome:
    merkeys: list[str]
    mode: str                       # 'barcode' | 'exact' | 'partial' | 'fuzzy' | 'empty'
    did_you_mean: str = ''
    suggestions: list[dict] = field(default_factory=list)


def _fts_search(conn: sqlite3.Connection, match_expr: str, limit: int) -> list[str]:
    if not match_expr:
        return []
    weights = ', '.join(str(w) for w in _FTS_WEIGHTS)
    try:
        rows = conn.execute(
            f"""
            SELECT merkey
            FROM products_fts
            WHERE products_fts MATCH ?
            ORDER BY bm25(products_fts, {weights})
            LIMIT ?
            """,
            (match_expr, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r['merkey'] for r in rows]


def _barcode_search(conn: sqlite3.Connection, code: str, limit: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT merkey FROM products
        WHERE active = 1 AND (
            merkey = ? OR barcode = ? OR all_barcodes LIKE ? OR barcode LIKE ?
        )
        LIMIT ?
        """,
        (code, code, f'%{code}%', f'{code}%', limit),
    ).fetchall()
    return [r['merkey'] for r in rows]


def _suggest(conn: sqlite3.Connection, tokens: list[str], corrected: list[str]) -> list[dict]:
    """Closest brands/categories for a thin/empty result, for "try these" chips."""
    vocab_terms = _vocab(conn)
    out: list[dict] = []
    seen: set[str] = set()
    for tok in tokens:
        if len(tok) < 3 or tok.isdigit():
            continue
        for cand in difflib.get_close_matches(tok, vocab_terms, n=3, cutoff=0.7):
            if cand in seen:
                continue
            seen.add(cand)
            row = conn.execute(
                "SELECT kind FROM search_vocab WHERE term = ?", (cand,)
            ).fetchone()
            out.append({'term': cand, 'kind': row['kind'] if row else 'word'})
            if len(out) >= 6:
                return out
    return out


def run_search(conn: sqlite3.Connection, raw_q: str,
               synonyms: dict[str, list[str]] | None = None,
               limit: int = _CANDIDATE_LIMIT) -> SearchOutcome:
    """Full cascade. Returns ranked merkeys + mode + did-you-mean + suggestions."""
    parsed = parse_query(raw_q, synonyms)

    if parsed.is_barcode:
        hits = _barcode_search(conn, parsed.barcode, limit)
        return SearchOutcome(merkeys=hits, mode='barcode' if hits else 'empty')

    if not parsed.groups:
        return SearchOutcome(merkeys=[], mode='empty')

    # 1) strict — every CORE word group must match (sizes/counts don't block).
    and_groups = parsed.core_groups or parsed.groups
    strict = _fts_search(conn, _fts_match_expr(and_groups, 'AND'), limit)
    if len(strict) >= _THIN_RESULTS:
        return SearchOutcome(merkeys=strict, mode='exact')

    # 2) widen — any token group may match (keep strict hits ranked first).
    loose = _fts_search(conn, _fts_match_expr(parsed.groups, 'OR'), limit)
    merged = list(dict.fromkeys([*strict, *loose]))
    if merged:
        return SearchOutcome(merkeys=merged, mode='exact' if strict else 'partial')

    # 3) typo-tolerant — correct tokens against the real vocabulary, retry.
    corrected, changed = correct_tokens(conn, parsed.flat_tokens)
    if changed:
        corr_groups = [[t] for t in corrected]
        fuzzy = _fts_search(conn, _fts_match_expr(corr_groups, 'OR'), limit)
        if fuzzy:
            return SearchOutcome(merkeys=fuzzy, mode='fuzzy',
                                 did_you_mean=' '.join(corrected),
                                 suggestions=_suggest(conn, parsed.flat_tokens, corrected))

    # 4) last resort — retrieve products for the closest suggested terms so the
    #    shopper still sees something relevant instead of a dead end.
    suggestions = _suggest(conn, parsed.flat_tokens, corrected)
    if suggestions:
        sug_groups = [[s['term']] for s in suggestions]
        rescued = _fts_search(conn, _fts_match_expr(sug_groups, 'OR'), limit)
        if rescued:
            return SearchOutcome(merkeys=rescued, mode='fuzzy',
                                 did_you_mean=suggestions[0]['term'],
                                 suggestions=suggestions)

    return SearchOutcome(merkeys=[], mode='empty', suggestions=suggestions)


# ---------------------------------------------------------------------------
# Optional synonym CSV (Phase 3 growth path)
# ---------------------------------------------------------------------------

def load_synonyms(csv_path) -> dict[str, list[str]]:
    """Merge the seed map with an optional CSV of `term,alias1;alias2` rows.
    Missing/unreadable file -> just the seed map."""
    merged = {k: list(v) for k, v in _SEED_SYNONYMS.items()}
    try:
        import csv
        with open(csv_path, newline='', encoding='utf-8') as fh:
            for row in csv.reader(fh):
                if not row or row[0].strip().startswith('#') or len(row) < 2:
                    continue
                term = row[0].strip().lower()
                aliases = [a.strip().lower() for a in re.split(r'[;,|]', row[1]) if a.strip()]
                if term and aliases:
                    merged.setdefault(term, [])
                    merged[term] = list(dict.fromkeys(merged[term] + aliases))
    except (FileNotFoundError, OSError):
        pass
    return merged
