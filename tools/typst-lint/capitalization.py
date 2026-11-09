"""Bounded rule component for trusted parser-certified contexts, not a parser.

This module does not discover contexts. The bounded paragraph_rules source
adapter must establish effective language, helper provenance, body ownership and
literal first-token classification. A config default cannot override
unknown/dynamic source-language evidence.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from enum import Enum
import re

from edit_plan import Edit
from typst_lint import Diagnostic, InputError, digest, load_rules, relative

RULE_ID = 'pde.paragraph-capitalization'
# Explicit editorial convention, not a test of mathematical meaning.
WORDS = frozenset(b'an the this these that those we it let assume suppose consider '
                  b'define denote hence thus therefore since if for when then now '
                  b'note recall observe'.split())


class StartKind(Enum):
    LITERAL_WORD = 'literal-word'
    MATH = 'math'
    CODE = 'code'
    REFERENCE = 'reference'
    WRAPPER = 'wrapper'
    QUOTE = 'quote'
    INTERPOLATION = 'interpolation'
    ESCAPE = 'escape'
    UNKNOWN = 'unknown'


class BodyKind(Enum):
    ORDINARY_PROSE = 'ordinary-prose'
    OTHER = 'other'
    UNKNOWN = 'unknown'


class MarkerBinding(Enum):
    PARAGRAPH_TAB = 'resolved-paragraph-tab'
    OTHER = 'other'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Context:
    source_sha256: str
    path: str
    body_start: int
    body_end: int
    marker_start: int
    marker_end: int
    first_start: int
    first_end: int
    expected_first: bytes
    start_kind: StartKind
    body_kind: BodyKind
    marker_binding: MarkerBinding
    effective_language: str | None
    suppressed: bool = False


@dataclass(frozen=True)
class Result:
    edit: Edit | None = None
    diagnostic: Diagnostic | None = None
    skip_reason: str | None = None


def _rule_metadata(catalog):
    """An explicit catalog, including an empty one, never selects ambient data."""
    if type(catalog) not in (tuple, list):
        raise InputError('Capitalization requires explicit rule metadata records')
    if any(not isinstance(record, Mapping) or type(record.get('id')) is not str
           or not record['id'] for record in catalog):
        raise InputError('Invalid capitalization catalog record identity')
    records = [record for record in catalog if record['id'] == RULE_ID]
    if (len(records) != 1
            or any(type(records[0].get(name)) is not str or not records[0][name]
                   for name in ('grade', 'source'))):
        raise InputError('Missing or invalid capitalization rule metadata')
    return records[0]


def evaluate(source: bytes, context: Context, *, catalog=None) -> Result:
    """Return one byte edit/finding, or an explicit refusal/nonapplicability reason.

    Effective English tags match en(?:-[A-Za-z0-9]{2,8})* case-insensitively.
    First-token spans
    come from parser-certified literal text, never from scanning ahead. Intervening comments
    are conservatively unsupported, even if the parser classified them safely.
    Marker and prose must be adjacent source lines: exactly one LF or CRLF,
    with horizontal whitespace only. Blank intervening lines detach ownership.
    """
    # Validate supplied evidence even on a non-editing path. None alone retains
    # the old lazy ambient lookup; an empty/invalid supplied catalog cannot
    # silently become disk authority.
    selected_rule = None if catalog is None else _rule_metadata(catalog)

    def skip(reason):
        return Result(skip_reason=reason)

    if type(source) is not bytes or type(context) is not Context:
        return skip('invalid-immutable-input-or-context')
    if not isinstance(context.start_kind, StartKind) or not isinstance(context.body_kind, BodyKind) or not isinstance(context.marker_binding, MarkerBinding):
        return skip('invalid-context-types')
    if type(context.suppressed) is not bool or type(context.expected_first) is not bytes:
        return skip('invalid-context-types')
    if context.effective_language is not None and type(context.effective_language) is not str:
        return skip('invalid-context-types')
    if context.source_sha256 != digest(source):
        return skip('stale-source-context')
    try:
        relative(context.path)
        source.decode('utf-8')
    except (ValueError, UnicodeError):
        return skip('invalid-path-or-utf8')
    positions = (context.body_start, context.body_end, context.marker_start,
                 context.marker_end, context.first_start, context.first_end)
    if any(type(p) is not int for p in positions):
        return skip('invalid-span-types')
    bs, be, ms, me, start, end = positions
    if not 0 <= bs <= ms < me <= start < end <= be <= len(source):
        return skip('invalid-span-bounds-or-body')
    try:
        for position in positions:
            source[:position].decode('utf-8')
    except UnicodeError:
        return skip('span-splits-utf8')
    if source[start:end] != context.expected_first:
        return skip('stale-first-token')
    if context.marker_binding is not MarkerBinding.PARAGRAPH_TAB:
        return skip('unresolved-or-unrelated-marker')
    if source[ms:me] != b'#paragraph_tab':
        return skip('marker-span-mismatch')
    if context.body_kind is not BodyKind.ORDINARY_PROSE:
        return skip('unsupported-body-context')
    if context.start_kind is not StartKind.LITERAL_WORD:
        return skip('first-token-not-direct-literal-word')
    # Restrict the literal marker to its own source line inside the certified body.
    line_start = max(bs, source.rfind(b'\n', bs, ms) + 1)
    if source[line_start:ms].strip(b' \t\r'):
        return skip('marker-not-standalone')
    gap = source[me:start]
    if re.fullmatch(rb'[ \t]*\r?\n[ \t]*', gap) is None:
        return skip('unsupported-content-before-first-word')
    if context.effective_language is None or re.fullmatch(
            r'en(?:-[A-Za-z0-9]{2,8})*', context.effective_language, re.IGNORECASE | re.ASCII) is None:
        return skip('effective-language-not-explicit-english')
    if context.suppressed:
        return skip('suppressed')
    word = context.expected_first
    if len(word) == 1:
        return skip('single-letter-initial')
    if not word.isascii() or not word.isalpha():
        return skip('initial-word-not-ascii-letters')
    if end == be or source[end:end + 1] not in (b' ', b'\t', b'\r', b'\n'):
        return skip('complete-word-followed-by-whitespace-required')
    if word.lower() not in WORDS:
        return skip('initial-word-outside-editorial-allowlist')
    if word == word[:1].upper() + word[1:].lower() or word.isupper():
        return skip('already-uppercase')
    if not word.islower():
        return skip('mixed-case-initial-word')
    rule = (_rule_metadata(load_rules()[0]['rules'])
            if selected_rule is None else selected_rule)
    line = source[:start].count(b'\n') + 1
    column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
    return Result(Edit(start, start + 1, word[:1].upper()),
                  Diagnostic(RULE_ID, rule['grade'], context.path, line, column,
                             'Capitalize the supported English paragraph-initial word.', rule['source']))
