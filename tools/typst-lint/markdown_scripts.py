"""Bounded Markdown examples, official Typst trees, original-byte proposals.

This is not a CommonMark parser or a source writer. Unsupported Markdown is
quarantined and reported explicitly; no prose or binding-dependent rules run.
"""
from collections import Counter
from dataclasses import dataclass, field, replace
import re

from edit_plan import Edit, plan
from script_rule import RULE_ID, analyze as scripts
from suppression import SuppressionError
from syntax_client import MAX_INPUT, parse
from typst_lint import InputError, SafetyError, relative, require

MAX_EXAMPLES = 256
MAX_ATTEMPTS = 256
MAX_REASONS = 256
MAX_EXAMPLE_BYTES = 65_536
TEACHING_SENTENCE = b'Do not write bare scripts such as `$X^i$` or `$X_j$`.'
REASON_LIMIT = 'Additional unsupported Markdown contexts omitted after reason limit'


@dataclass(frozen=True)
class MarkdownExample:
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class MarkdownExtraction:
    examples: tuple[MarkdownExample, ...] = ()
    skips: tuple[str, ...] = ()
    exemptions: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class MarkdownResult:
    diagnostics: tuple = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple = ()
    extraction: MarkdownExtraction = MarkdownExtraction()


def _source(source):
    require(type(source) is bytes and len(source) <= MAX_INPUT,
            'Markdown source must be immutable bytes of at most 1 MiB')
    try:
        source.decode('utf-8')
    except UnicodeError as error:
        raise InputError('Markdown source must be UTF-8') from error


@dataclass(frozen=True)
class MarkdownContext:
    """Actual captured source for independent audit; no filesystem rereads."""
    source: bytes = field(repr=False)
    path: str
    ignored: frozenset[str] = frozenset()

    def __post_init__(self):
        _source(self.source)
        relative(self.path)
        require(self.path.endswith('.md'), 'Markdown analysis requires a .md path')
        require(type(self.ignored) is frozenset and all(type(r) is str for r in self.ignored),
                'Markdown ignored rule IDs must be an immutable frozenset')

    def analyze(self):
        return analyze(self.source, self.path, ignored=self.ignored)


def _line(source, position):
    return source.count(b'\n', 0, position) + 1


def _position(source, position):
    return (_line(source, position),
            len(source[source.rfind(b'\n', 0, position) + 1:position].decode('utf-8')) + 1)


def _offset(source, diagnostic):
    lines = source.split(b'\n')
    require(1 <= diagnostic.line <= len(lines), 'Invalid example diagnostic line')
    text = lines[diagnostic.line - 1].decode('utf-8')
    require(1 <= diagnostic.column <= len(text) + 1, 'Invalid example diagnostic column')
    return (sum(len(line) + 1 for line in lines[:diagnostic.line - 1])
            + len(text[:diagnostic.column - 1].encode('utf-8')))


def _escaped(raw, position):
    cursor = position
    while cursor and raw[cursor - 1] == 92:
        cursor -= 1
    return (position - cursor) % 2 == 1


def _run(raw, position, token):
    end = position
    while end < len(raw) and raw[end] == token:
        end += 1
    return end


def _backtick_close(raw, start, size):
    while start < len(raw):
        at = raw.find(b'`', start)
        if at < 0:
            return None
        end = _run(raw, at, 96)
        # Backslashes have no escaping role inside an existing code span.
        if end - at == size:
            return at, end
        start = end
    return None


def _dollar_close(raw, start, size):
    while start < len(raw):
        at = raw.find(b'$', start)
        if at < 0:
            return None
        end = _run(raw, at, 36)
        if end - at == size and not _escaped(raw, at):
            return at, end
        start = end
    return None


def _bounded(raw):
    value = raw[:MAX_EXAMPLE_BYTES]
    # The complete captured input is already validated UTF-8. Only a probe's
    # byte budget can split its last code point; no code point is normalized.
    while value:
        try:
            value.decode('utf-8')
            break
        except UnicodeDecodeError:
            value = value[:-1]
    return value


def _html_end(raw):
    if raw.startswith(b'<!--'):
        return b'-->'
    if raw.startswith(b'<![CDATA['):
        return b']]>'
    if raw.startswith(b'<?'):
        return b'?>'
    tag = re.match(br'<(script|pre|style|textarea)(?:[ \t>]|$)', raw, re.I)
    if tag:
        return b'</' + tag[1].lower() + b'>'
    if re.match(br'<![A-Z]', raw):
        return b'>'
    return None


def _fence(raw):
    """Markdown delimiter recognition only; never inspect Typst operands."""
    match = re.fullmatch(br'(`{3,}|~{3,})(.*)', raw)
    if match is None:
        return None
    marker, info = match.groups()
    return marker[:1], len(marker), info.strip(b' \t')


def _container(raw):
    cursor = 0
    while cursor < len(raw) and raw[cursor] in b' \t':
        cursor += 1
    while cursor < len(raw) and raw[cursor] == 62:
        cursor += 1
        while cursor < len(raw) and raw[cursor] in b' \t':
            cursor += 1
    match = re.compile(br'(?:[-+*]|[0-9]+[.)])[ \t]+').match(raw, cursor)
    if match:
        cursor = match.end()
    return raw[cursor:]


def _equation(tree):
    """A real closed first Equation, including a closed malformed equation."""
    root = tree.nodes[0]
    if not root.children:
        return None
    node = tree.nodes[root.children[0]]
    children = [tree.nodes[i] for i in node.children]
    if (node.kind != 'Equation' or node.start != 0 or len(children) < 2
            or children[0].kind != 'Dollar' or children[-1].kind != 'Dollar'):
        return None
    return node


def extract(source: bytes):
    """Extract a documented bounded subset, retaining exact contiguous spans.

    The official parser certifies bare-inline Equation boundaries, including
    dollars in strings and escapes. It does not parse surrounding Markdown.
    """
    _source(source)
    if b'\r' in source.replace(b'\r\n', b''):
        return MarkdownExtraction(skips=('Bare CR Markdown line endings are unsupported',))
    examples, skips, exemptions = [], [], []
    seen_reasons = set()
    fence = None
    code_run = 0
    dollar_continuation = False
    display = 0
    container = False
    brackets = False
    html = False
    frontmatter = False
    offset = 0
    attempts = 0

    def skip(message, at):
        if len(skips) >= MAX_REASONS:
            if REASON_LIMIT not in seen_reasons:
                seen_reasons.add(REASON_LIMIT)
                skips.append(REASON_LIMIT)
            return
        value = f'{message} at line {_line(source, at)}'
        if value not in seen_reasons:
            seen_reasons.add(value)
            skips.append(value)

    def consume(at):
        nonlocal attempts
        if attempts >= MAX_ATTEMPTS:
            skip('Markdown extraction probe/example budget exhausted; remaining Markdown is unsupported', at)
            return False
        attempts += 1
        return True

    def add(kind, start, end):
        if end - start > MAX_EXAMPLE_BYTES:
            skip('Markdown example exceeds the 64 KiB analysis bound', start)
        elif len(examples) >= MAX_EXAMPLES:
            skip('Markdown example count limit reached; remaining examples are unsupported', start)
            return False
        else:
            if not consume(start):
                return False
            examples.append(MarkdownExample(kind, start, end))
        return True

    lines = source.split(b'\n')
    for number, raw in enumerate(lines):
        after = offset + len(raw) + (number < len(lines) - 1)
        body = raw[:-1] if raw.endswith(b'\r') else raw
        base = offset
        offset = after
        if number == 0 and body.startswith(b'\xef\xbb\xbf'):
            body, base = body[3:], base + 3
        if fence is not None:
            marker, size, start, supported, nested = fence
            if nested:
                # No CommonMark container ownership is certified. Stripping
                # unrelated list/quote prefixes can invent a false closer.
                continue
            candidate = _fence(body)
            if candidate and candidate[0] == marker and candidate[1] >= size and not candidate[2]:
                fence = None
                if supported and not add('typst-fence', start, base):
                    break
            continue
        i = 0
        if code_run:
            close = _backtick_close(body, 0, code_run)
            if close is None:
                continue
            i, code_run = close[1], 0
        if display:
            close = _dollar_close(body, i, display)
            if close is None:
                continue
            i, display = close[1], 0
        if dollar_continuation:
            # No Markdown math grammar certifies a safe resynchronization
            # dollar in a string/comment on another line. Quarantine the rest.
            continue
        if frontmatter:
            if body in (b'---', b'...'):
                frontmatter = False
            continue
        if html:
            if type(html) is bytes:
                end = body.lower().find(html)
                if end < 0:
                    continue
                i = end + len(html)
                html = False
            else:
                if not body.strip():
                    html = False
                continue
        if brackets:
            if not body.strip():
                brackets = False
            continue
        if not body.strip():
            container = False
            continue
        if number == 0 and body == b'---':
            frontmatter = True
            skip('Markdown front matter is unsupported', base)
            continue
        if i == 0:
            opened = _fence(body)
            nested = False
            if opened is None:
                opened = _fence(_container(body))
                nested = opened is not None
            if opened:
                marker, size, info = opened
                typst = info.split(None, 1)[:1] == [b'typst']
                supported = typst and info == b'typst' and not nested and not container
                if nested or container:
                    skip('Indented/container fence boundary is unsupported; remaining Markdown is quarantined', base)
                    nested = True
                if typst and not supported:
                    skip('Indented/container or extended-info Typst fence is unsupported', base)
                fence = (marker, size, after, supported, nested)
                continue
            if (container or body[:1] in (b' ', b'\t', b'>')
                    or re.match(br'(?:[-+*]|[0-9]+[.)])[ \t]+', body)):
                container = True
                skip('Indented/container Markdown is unsupported', base)
                continue
        # Preflight the complete line before collecting any examples. Labels,
        # images, reference definitions and multiline destinations share these
        # delimiters. Their paragraph is quarantined without guessing label
        # ownership. Bracket-bearing inline math is an explicit initial limit.
        if b'[' in body[i:] or b']' in body[i:]:
            # Some opaque HTML openers contain brackets themselves. Preserve
            # their stronger cross-blank-line boundary instead of treating
            # them as an ordinary Markdown bracket paragraph.
            at = body.find(b'<', i)
            terminator = _html_end(body[at:]) if at >= 0 else None
            if terminator is not None:
                skip('HTML/link Markdown context is unsupported', base + at)
                html = terminator
                end = body.lower().find(terminator, at + 1)
                if end >= 0:
                    html = False
                continue
            skip('Bracket/link Markdown paragraph is unsupported', base + i)
            brackets = True
            continue
        while i < len(body):
            if body.startswith(TEACHING_SENTENCE, i) and (i == 0 or body[i-1] in b' \t.!?:;'):
                end = i + len(TEACHING_SENTENCE)
                if end == len(body) or body[end] in b' \t':
                    exemptions.append((base + i, base + end))
                    i = end
                    continue
            if body[i] in (36, 96, 60) and _escaped(body, i):
                i += 1
                continue
            if body[i] == 60 or body.startswith((b'](', b']:'), i):
                skip('HTML/link Markdown context is unsupported', base + i)
                html = (_html_end(body[i:]) or True) if body[i] == 60 else False
                if type(html) is bytes:
                    # A same-line close is still quarantined with this whole
                    # line, but must not hide the following source lines.
                    search = body.lower().find(html, i + 1)
                    if search >= 0:
                        html = False
                break
            if body[i] == 96:
                end = _run(body, i, 96)
                close = _backtick_close(body, end, end - i)
                if close is None:
                    skip('Multiline or unclosed Markdown code span is unsupported', base + i)
                    code_run = end - i
                    break
                a, b = close
                content = body[end:a]
                if content.startswith(b'$') and content.endswith(b'$') and not content.startswith(b'$$'):
                    if len(content) > MAX_EXAMPLE_BYTES:
                        skip('Markdown example exceeds the 64 KiB analysis bound', base + end)
                    else:
                        if not consume(base + end):
                            return MarkdownExtraction(tuple(examples), tuple(skips), tuple(exemptions))
                        tree = parse(content)
                        equation = _equation(tree)
                        if equation is not None and equation.end == len(content):
                            if not add('inline-code-math', base + end, base + a):
                                return MarkdownExtraction(tuple(examples), tuple(skips), tuple(exemptions))
                        else:
                            skip('Code span is not one bounded Typst equation', base + end)
                elif b'$' in content:
                    skip('Mixed-content code-span examples are unsupported', base + end)
                i = b
                continue
            if body[i] == 36:
                if body.startswith(b'$$', i):
                    skip('Markdown display-dollar delimiters are unsupported', base + i)
                    end = _run(body, i, 36)
                    close = _dollar_close(body, end, end - i)
                    if close is None:
                        display = end - i
                        break
                    i = close[1]
                    continue
                suffix = _bounded(body[i:])
                if not consume(base + i):
                    return MarkdownExtraction(tuple(examples), tuple(skips), tuple(exemptions))
                tree = parse(suffix)
                equation = _equation(tree)
                if equation is None:
                    skip('Multiline, unclosed or over-limit Markdown math is unsupported', base + i)
                    dollar_continuation = True
                    break
                content = suffix[:equation.end]
                if b'`' in content or content.endswith(b'$$'):
                    skip('Ambiguous Markdown math delimiter context is unsupported', base + i)
                elif not add('inline-math', base + i, base + i + equation.end):
                    return MarkdownExtraction(tuple(examples), tuple(skips), tuple(exemptions))
                i += equation.end
                continue
            i += 1
    if fence is not None and not fence[4]:
        skip('Unclosed Markdown fence is unsupported', fence[2])
    if frontmatter:
        skip('Unclosed Markdown front matter is unsupported', 0)
    return MarkdownExtraction(tuple(examples), tuple(skips), tuple(exemptions))


def _map_reasons(reasons, transform):
    return tuple(re.sub(r'at byte (\d+)', lambda m: f'at byte {transform(int(m[1]))}', value)
                 for value in reasons)


def _identity(source, findings, transform=lambda value: value):
    return Counter((d.rule_id, d.grade, d.path, transform(_offset(source, d)), d.explanation, d.source)
                   for d in findings)


def analyze(source: bytes, path: str, *, ignored=frozenset(), _verify=True):
    """Single-pass verified insertion proposals; parse/config errors never ignored.

    As in source_analysis, config `ignored` withholds edits while retaining
    diagnostics for report-level path filtering. `.suppressed` is inline-only.
    """
    context = MarkdownContext(source, path, ignored)
    extraction = extract(context.source)
    findings, suppressed, edits, errors = [], [], [], []
    skips = list(extraction.skips)
    for example in extraction.examples:
        raw = source[example.start:example.end]
        tree = parse(raw)
        if example.kind != 'typst-fence':
            for node in tree.nodes:
                if (node.kind == 'LineComment'
                        and raw[node.start:node.end][2:].lstrip(b' \t').startswith(b'typst-lint:')):
                    raise SuppressionError(f'Inline Markdown examples at line {_line(source, example.start)} '
                                           'require a path/rule configuration ignore')
        try:
            result = scripts(tree, path, _verify=False)
        except SuppressionError as error:
            message = re.sub(r'at line (\d+)',
                             lambda m: f'at line {_line(source, example.start) + int(m[1]) - 1}', str(error))
            raise SuppressionError(message) from error
        def mapped(diagnostic):
            line, column = _position(source, example.start + _offset(raw, diagnostic))
            return replace(diagnostic, line=line, column=column)
        findings.extend(mapped(d) for d in result.diagnostics)
        suppressed.extend(mapped(d) for d in result.suppressed)
        errors.extend((example.start + a, example.start + b) for a, b in result.parse_errors)
        for reason in _map_reasons(result.skips, lambda value: example.start + value):
            if len(skips) < MAX_REASONS:
                skips.append(reason)
            elif REASON_LIMIT not in skips:
                skips.append(REASON_LIMIT)
        for edit in result.edits:
            if (edit.start != edit.end or not 0 <= edit.start <= len(raw)
                    or not edit.replacement or edit.replacement.strip(b'()')):
                raise SafetyError('Markdown script backend proposed a non-parenthesis insertion')
            edits.append(Edit(example.start + edit.start, example.start + edit.end, edit.replacement))
    if errors or RULE_ID in ignored:
        edits = []
    proposed = plan(source, edits)
    if proposed.deferred:
        raise SafetyError('Overlapping Markdown example edits')
    edits = proposed.edits
    if edits and _verify:
        candidate = proposed.candidate()
        if len(candidate) > MAX_INPUT:
            skips.append('Markdown candidate exceeds the 1 MiB source bound; no edits proposed')
            edits = ()
        else:
            final = analyze(candidate, path, ignored=ignored, _verify=False)
            transform = lambda value: value + sum(len(e.replacement) for e in edits if e.start <= value)
            expected = tuple((e.kind, transform(e.start), transform(e.end)) for e in extraction.examples)
            actual = tuple((e.kind, e.start, e.end) for e in final.extraction.examples)
            exemptions = tuple((transform(a), transform(b)) for a, b in extraction.exemptions)
            if (final.edits or final.diagnostics or final.parse_errors or expected != actual
                    or exemptions != final.extraction.exemptions
                    or _map_reasons(skips, transform) != final.skips
                    or _identity(source, suppressed, transform) != _identity(candidate, final.suppressed)):
                skips.append('Markdown candidate failed extraction/parse/suppression/idempotence verification; no edits proposed')
                edits = ()
    key = lambda d: (d.line, d.column, d.rule_id)
    return MarkdownResult(tuple(sorted(findings, key=key)), tuple(edits), tuple(skips),
                          tuple(sorted(set(errors))), tuple(sorted(suppressed, key=key)), extraction)
