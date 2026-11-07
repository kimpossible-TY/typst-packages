"""Trusted closed-core repair bridge for the explicit offline CLI profile.

Only one explicitly configured literal .typ input is admitted. This does not
certify general Typst projects, the PDE pack, dependency graphs or compilation.
The runtime/backend code is trusted; serialized reports cannot confer authority.
The caller must hold the real offline-exclusive assertion for the whole context.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
import tomllib
from types import MappingProxyType
from typing import Mapping

from candidate_convergence import converge
from closed_core import (CORE_RULE_IDS as ADMISSION_CORE_RULE_IDS, ClosedCoreError,
                         certify, verify_proof)
from edit_plan import Edit, plan
from script_rule import RULE_ID as SCRIPT, ScriptResult, analyze as analyze_script
from syntax_client import parse
from source_profiles import (CLOSED_CORE_SOURCE_PROFILE, grammar_for_source_profile,
                             validate_source_profile)
from transactions import (DEFAULT_READ_LIMITS, CommittedSnapshot, EvidenceInput, GraphAnalysis,
                          GraphSnapshot, ProtectedTransaction, SourceEdit,
                          TransactionError)
from typst_lint import CATALOG, InputError, digest, parse_rules, relative

REVIEWED_CATALOG_SHA256 = '955b0c721027369ec1fd100a7da00fc7304693bb40e2916904c6f3b06fae1826'
# Compiled authority, never a subset selected from a mutable catalog/config.
_CORE_POLICIES = MappingProxyType({
    'core.parenthesized-scripts': ('warning', 'automatic'),
    'core.scoped-function-call': ('error', 'automatic'),
    'core.scoped-marker-tags': ('warning', 'diagnostic-only'),
    'core.scoped-anchor-access': ('warning', 'conditional'),
    'core.scoped-reference-access': ('warning', 'conditional'),
    'core.local-overlay-api': ('warning', 'diagnostic-only'),
    'core.annotation-in-display': ('error', 'diagnostic-only'),
    'core.annotation-marker-bounds': ('error', 'diagnostic-only'),
    'core.unique-scope-namespace': ('error', 'diagnostic-only'),
    'core.project-ref-arguments': ('error', 'conditional'),
    'core.project-ref-catalog-target': ('error', 'diagnostic-only'),
    'core.single-book-show': ('error', 'diagnostic-only'),
    'core.no-duplicate-book-reset': ('error', 'conditional'),
    'core.no-set-theme': ('error', 'diagnostic-only'),
    'core.theme-in-context': ('warning', 'conditional'),
})
CORE_RULE_IDS = frozenset(_CORE_POLICIES)
_PDE_RULE_IDS = frozenset({
    'pde.import-order', 'pde.no-redundant-facade-import',
    'pde.paragraph-marker-gap', 'pde.paragraph-marker-target',
    'pde.prose-paragraph-marker', 'pde.continuation-line',
    'pde.display-continuation-gap', 'pde.proof-trailing-gap',
    'pde.footnote-trailing-gap', 'pde.prose-footnote-punctuation',
    'pde.aligned-footnote-spacer', 'pde.unaligned-footnote-no-spacer',
    'pde.prose-footnote-no-spacer', 'pde.standalone-display',
    'pde.text-only-highlight', 'pde.theme-cancelmark',
    'pde.flowbox-colored-border', 'pde.paragraph-capitalization',
})
_CONFIG = '.typst-lint.toml'
_RULE_FIELDS = frozenset({'id', 'grade', 'source', 'description', 'repair',
                          'rewrite', 'preconditions'})


class ProtectedCoreError(TransactionError):
    """The deliberately narrow protected profile was not fully proven."""


class ProtectedCoreInputError(ProtectedCoreError, InputError):
    """Invalid explicit target/config/catalog, still caught by the old bridge API."""
    def __init__(self, message, *, error_code='invalid_configuration', phase='configuration'):
        self.error_code = error_code
        self.phase = phase
        super().__init__(message)


def _input_require(condition, message, **category):
    if not condition:
        raise ProtectedCoreInputError(message, **category)


def _require(condition, message):
    if not condition:
        raise ProtectedCoreError(message)


def _literal_target(target):
    try:
        relative(target)
    except InputError as error:
        raise ProtectedCoreInputError(str(error), error_code='invalid_target', phase='arguments') from error
    _input_require(target.endswith('.typ') and not any(c in target for c in '*?[]{}'),
                   'Protected core requires one literal canonical .typ target',
                   error_code='invalid_target', phase='arguments')
    return target


def _config(raw, target):
    _input_require(type(raw) is bytes, 'Explicit present configuration bytes required')
    try:
        data = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ProtectedCoreInputError(f'Invalid protected configuration: {error}') from error
    _input_require({'rule-set', 'include'} <= data.keys()
             and not (data.keys() - {'rule-set', 'include', 'entrypoint'}),
             'Protected core configuration requires explicit rule-set/include and no unsupported keys')
    _input_require(data['rule-set'] == 'core', 'Protected core requires the core pack')
    _input_require(type(data['include']) is list and data['include'] == [target],
             'Protected core include must select exactly the literal target')
    _input_require('entrypoint' not in data or data['entrypoint'] == target,
             'Protected core entrypoint must be absent or the same literal target')
    return digest(raw)


def _catalog_require(condition, message):
    _input_require(condition, message, error_code='invalid_catalog', phase='catalog')


def _catalog(raw):
    _catalog_require(type(raw) is bytes, 'Complete present catalog evidence required')
    _catalog_require(digest(raw) == REVIEWED_CATALOG_SHA256,
             'Protected core requires the exact reviewed full catalog digest')
    try:
        data, sha256 = parse_rules(raw)
    except (InputError, ValueError, UnicodeDecodeError) as error:
        raise ProtectedCoreInputError(f'Invalid protected rule catalog: {error}', error_code='invalid_catalog', phase='catalog') from error
    _catalog_require(set(data) == {'schema', 'version', 'packs', 'rules'},
             'Unknown protected catalog fields')
    records = data['rules']
    _catalog_require({r['id'] for r in records} == CORE_RULE_IDS | _PDE_RULE_IDS,
             'Protected catalog IDs differ from the compiled reviewed set')
    _catalog_require(frozenset(ADMISSION_CORE_RULE_IDS) == CORE_RULE_IDS,
             'Admission and protected core rule sets disagree')
    for record in records:
        _catalog_require(set(record) == _RULE_FIELDS, 'Unknown protected rule metadata')
        if record['id'] in CORE_RULE_IDS:
            _catalog_require((record['grade'], record['repair']) == _CORE_POLICIES[record['id']],
                     'Protected core rule repair metadata changed')
    # No mutable list/dict crosses into the actual script backend.
    return tuple(MappingProxyType(dict(record)) for record in records), sha256


def _attachment_roles(tree):
    return tuple(tuple(role for role, _ in node.operands)
                 for node in tree.nodes if node.kind == 'MathAttach')


def _script_ownership(tree):
    """Retain wrapper/equation and nested-operand owners through grouping.

    Math grouping is transparent here. Exact operand-boundary insertions and
    raw-byte preservation are checked separately, so this is not permission for
    any other rewrite or for moving a script to an equal-looking equation.
    """
    wrappers = frozenset(('Heading', 'ListItem', 'EnumItem', 'Strong', 'Emph'))
    owners, attachments = [], []
    counts = {'wrapper': 0, 'equation': 0, 'attachment': 0}

    def visit(index, enclosing=(), operands=()):
        node = tree.nodes[index]
        if node.kind in wrappers:
            counts['wrapper'] += 1
            owner = (node.kind, counts['wrapper'])
            owners.append((owner, enclosing))
            enclosing += (owner,)
        elif node.kind == 'Equation':
            counts['equation'] += 1
            owner = ('Equation', counts['equation'], node.display)
            owners.append((owner, enclosing))
            enclosing += (owner,)
        if node.kind == 'MathAttach':
            counts['attachment'] += 1
            ordinal = counts['attachment']
            attachments.append((enclosing, operands, tuple(role for role, _ in node.operands)))
            roles = {child: role for role, child in node.operands}
            for child in node.children:
                visit(child, enclosing, operands + ((ordinal, roles.get(child, 'base')),))
        else:
            for child in node.children:
                visit(child, enclosing, operands)

    visit(0)
    return tuple(owners), tuple(attachments)


def _script_insertions(tree):
    """Independently account for both boundaries of every ungrouped operand."""
    operands = set()
    for node in tree.nodes:
        for _, operand_id in node.operands:
            operand = tree.nodes[operand_id]
            children = tuple(tree.nodes[i] for i in operand.children)
            if not (operand.kind == 'Math' and children
                    and children[0].kind == 'LeftParen' and children[-1].kind == 'RightParen'):
                operands.add((operand.start, operand.end))
    opens, closes = {}, {}
    for start, end in operands:
        opens[start] = opens.get(start, 0) + 1
        closes[end] = closes.get(end, 0) + 1
    return tuple(Edit(p, p, b')' * closes.get(p, 0) + b'(' * opens.get(p, 0))
                 for p in sorted(opens.keys() | closes.keys()))


def _operand_records(tree, path):
    """Semantic IDs survive inserted grouping; no byte offsets enter identity.

    This profile only inserts script parentheses and requires a clean final
    state. It does not claim stable IDs across arbitrary Typst rewrites.
    """
    records = []
    ordinal = 0
    for node in tree.nodes:
        if node.kind != 'MathAttach':
            continue
        ordinal += 1
        for role, operand_id in node.operands:
            operand = tree.nodes[operand_id]
            children = tuple(tree.nodes[i] for i in operand.children)
            parenthesized = (operand.kind == 'Math' and children
                             and children[0].kind == 'LeftParen'
                             and children[-1].kind == 'RightParen')
            line = tree.source[:operand.start].count(b'\n') + 1
            start = tree.source.rfind(b'\n', 0, operand.start) + 1
            column = len(tree.source[start:operand.start].decode('utf-8')) + 1
            identity = f'{path}:{SCRIPT}:attachment-{ordinal}:{role}'
            records.append((identity, line, column, bool(parenthesized)))
    return tuple(records)


def _findings(tree, path, result):
    expected = tuple(r for r in _operand_records(tree, path) if not r[3])
    observed = tuple((d.path, d.rule_id, d.line, d.column) for d in result.diagnostics)
    _require(observed == tuple((path, SCRIPT, line, col) for _, line, col, _ in expected),
             'Script diagnostics do not account for every admitted operand')
    return frozenset(identity for identity, _, _, _ in expected)


def _verify_selected_proof(proof, tree, path, source_profile):
    # A source refusal from certify is input classification. A proof which
    # fails independent verification is changed/foreign authority, not input.
    try:
        verify_proof(proof, tree, path, source_profile=source_profile)
    except ClosedCoreError as error:
        raise ProtectedCoreError(f'Protected admission proof changed: {error}') from error


def _script(tree, path, catalog, *, source_profile=CLOSED_CORE_SOURCE_PROFILE):
    proof = certify(tree, path, source_profile=source_profile)
    _verify_selected_proof(proof, tree, path, source_profile)
    _require(tuple(proof.evaluated_rule_ids) == (SCRIPT,)
             and frozenset(proof.not_applicable_rule_ids) == CORE_RULE_IDS - {SCRIPT}
             and frozenset(rid for rid, reason in proof.structural_reasons if reason)
             == CORE_RULE_IDS - {SCRIPT}, 'Incomplete per-input core admission proof')
    result = analyze_script(tree, path, _verify=False, catalog=catalog)
    _require(type(result) is ScriptResult
             and all(type(getattr(result, field)) is tuple for field in
                     ('diagnostics', 'edits', 'skips', 'parse_errors', 'suppressed')),
             'Immutable actual script analysis required')
    _require(not (result.skips or result.parse_errors or result.suppressed),
             'Skipped, suppressed or parse-error script analysis is unsupported')
    findings = _findings(tree, path, result)
    _require(bool(findings) == bool(result.edits),
             'Every script finding must have an actual repair in this profile')
    _require(result.edits == _script_insertions(tree),
             'Script proposals must insert exact grouping at every admitted operand boundary')
    return result, findings


@dataclass(frozen=True)
class _Pass:
    tree: object
    result: ScriptResult
    analysis: GraphAnalysis


@dataclass(frozen=True, slots=True)
class AppliedInsertion:
    path: str
    start: int
    end: int
    replacement: bytes
    rule_id: str = SCRIPT


@dataclass(frozen=True, slots=True)
class ProtectedCoreSummary:
    """Reporting only. Neither this value nor its serialization authorizes apply."""
    profile: str
    target: str
    original_sha256: str
    final_sha256: str
    required_rule_ids: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    applied_insertions: tuple[AppliedInsertion, ...]
    modified_paths: tuple[str, ...] = ()
    journal_id: str | None = None
    grammar_version: str = 'closed-core-markup-v1'


@dataclass(frozen=True, slots=True)
class _SourceSelection:
    source_profile: str
    grammar_version: str


@dataclass(frozen=True, slots=True)
class _CompileValidation:
    """The actual independently checked candidate/replay, never report data."""
    candidate: object
    replay: object
    proof: object
    root: Path
    target: str
    evidence: Mapping[str, bytes | None]
    config_sha256: str
    catalog_sha256: str
    selection: _SourceSelection


def _retain_authority(value):
    """Copy issued-object facts, not authority over arbitrary Python code changes.

    Only genuine validated objects are traversed here. Later checks follow this
    bounded shape, never snapshot a reflectively altered object graph.
    """
    kind = type(value)
    if kind in (str, bytes, int, bool, type(None), object):
        return kind, value
    if isinstance(value, Path):
        str(value)  # Populate the trusted original's path representation once.
        names = ('_raw_paths', '_parts', '_pparts', '_cached_cparts',
                 '_drv', '_root', '_tail_cached', '_str',
                 '_str_normcase_cached', '_parts_normcase_cached', '_hash')
        return kind, (value, tuple((name, _retain_authority(getattr(value, name)))
                                  for name in names if hasattr(value, name)))
    if kind in (tuple, list, frozenset):  # Lists occur only in trusted Path backing fields.
        return kind, tuple(_retain_authority(item) for item in value)
    if kind is MappingProxyType:
        return kind, (value, tuple((_retain_authority(key), _retain_authority(item))
                                  for key, item in value.items()))
    _require(is_dataclass(value) and value.__dataclass_params__.frozen,
             'Immutable independent validation facts required')
    return kind, tuple((item.name, _retain_authority(getattr(value, item.name)))
                       for item in fields(value))


def _matches_authority(value, retained):
    """Traverse only the trusted expected shape, even after reflective mutation."""
    kind, expected = retained
    if type(value) is not kind:
        return False
    if kind in (str, bytes):
        return len(value) == len(expected) and value == expected
    if kind in (int, bool, type(None)):
        return value == expected
    if kind is object:
        return value is expected
    if kind is MappingProxyType or issubclass(kind, Path):
        original, expected = expected
        if value is not original:
            return False
    if kind in (tuple, list, frozenset, MappingProxyType):
        if len(value) != len(expected):
            return False
        if kind in (tuple, list):
            return all(_matches_authority(item, fact) for item, fact in zip(value, expected))
        if kind is frozenset:
            return all(any(_matches_authority(item, fact) for fact in expected) for item in value)
        return all(_matches_authority(key, key_fact) and _matches_authority(item, item_fact)
                   for (key, item), (key_fact, item_fact) in zip(value.items(), expected))
    try:
        if not issubclass(kind, Path):
            state = getattr(value, '__dict__', None)
            if state is not None and (type(state) is not dict or len(state) != len(expected)
                                      or any(name not in state for name, _ in expected)):
                return False
        return all(_matches_authority(object.__getattribute__(value, name), fact)
                   for name, fact in expected)
    except AttributeError:
        return False


@dataclass(frozen=True, slots=True, init=False, eq=False)
class ClosedCoreCompileSnapshot:
    """Opaque live bridge receipt, not a compiler/resource-profile certificate."""
    root: Path
    target: str
    source: bytes = field(repr=False)
    source_sha256: str
    evidence: Mapping[str, bytes | None] = field(repr=False)
    required_rule_ids: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    config_sha256: str
    catalog_sha256: str
    _receipt: CommittedSnapshot = field(repr=False)
    _validation: _CompileValidation = field(repr=False)
    _owner: object = field(repr=False)
    source_profile: str
    grammar_version: str

    def __init__(self, *args, **kwargs):
        raise TypeError('ClosedCoreCompileSnapshot is issued only by compile_snapshot()')


class ProtectedCoreFix:
    """One internal closed-core transaction with independent provenance replay.

    `fault` is solely a transaction test hook. This class never asserts an
    OfflineExclusive lease, invokes a compiler, installs
    a dependency, or widens the fixed built-in catalog/profile. Explicit callers
    may request transaction-owned creation of the fixed CLI journal store.
    """
    def __init__(self, root: Path, target: str, *, journal_directory: Path,
                 lease, limits=DEFAULT_READ_LIMITS, fault=None,
                 create_journal_directory=False, source_profile=CLOSED_CORE_SOURCE_PROFILE):
        # Unknown selections are arguments, refused before transaction/root or
        # journal access. No profile is inferred from source or supplied proof.
        try:
            validate_source_profile(source_profile)
            grammar_version = grammar_for_source_profile(source_profile)
        except ValueError as error:
            raise ProtectedCoreInputError(str(error), error_code='invalid_profile', phase='arguments') from error
        self._selection = _SourceSelection(source_profile, grammar_version)
        self.source_profile = source_profile
        self.grammar_version = grammar_version
        self.target = _literal_target(target)
        self._tx = ProtectedTransaction(root, (self.target,),
            journal_directory=journal_directory, lease=lease, limits=limits,
            evidence=(EvidenceInput(Path(root) / _CONFIG, allow_missing=True), EvidenceInput(CATALOG)),
            fault=fault, create_journal_directory=create_journal_directory)
        self._config_path = str(self._tx.root / _CONFIG)
        self._catalog_path = str(Path(CATALOG).absolute())
        self._evidence_paths = frozenset((self._config_path, self._catalog_path))
        self._baseline = None
        self._active = False
        self._candidate = None
        self._history = []
        self._validated_summary = None
        self._summary = None
        self._compile_owner = object()
        self._compile_validation = None
        self._validation_authority = None
        self._applied_validation = None
        self._compile_snapshot = None
        self._compile_receipt = None

    @property
    def transaction(self):
        """Internal inspection/testing surface, not another bridge token issuer."""
        return self._tx

    @property
    def summary(self):
        """Immutable independently verified reporting, available only after apply."""
        _require(self._summary is not None, 'A successful protected apply is required for reporting')
        return self._summary

    def __enter__(self):
        self._assert_selection()
        self._tx.__enter__()
        try:
            snapshot = self._tx.snapshot
            self._context(snapshot)
            self._baseline = snapshot.sources[self.target]
            self._active = True
            return self
        except BaseException:
            self._tx.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, tb):
        self._active = False
        self._candidate = None
        self._validation_authority = None
        return self._tx.__exit__(exc_type, exc, tb)

    def _context(self, graph):
        self._assert_selection()
        _require(type(graph) is GraphSnapshot and graph.root == self._tx.root,
                 'Protected graph root differs from the captured transaction')
        _require(frozenset(graph.sources) == frozenset((self.target,))
                 and type(graph.sources[self.target]) is bytes,
                 'Protected graph requires exactly one captured source')
        _require(frozenset(graph.evidence) == self._evidence_paths,
                 'Additional or missing protected graph evidence is unsupported')
        # Both retained evidence values are actually parsed, never reread here.
        _config(graph.evidence[self._config_path], self.target)
        catalog, _ = _catalog(graph.evidence[self._catalog_path])
        return catalog

    def _assert_selection(self):
        _require(type(self.source_profile) is str and type(self.grammar_version) is str
                 and self.source_profile == self._selection.source_profile
                 and self.grammar_version == self._selection.grammar_version,
                 'Protected source profile or grammar changed during this session')

    def _assert_validation(self, validation):
        retained = self._validation_authority
        _require(retained is not None and validation is retained[0]
                 and _matches_authority(validation, retained[2])
                 and validation.candidate.sources is retained[1],
                 'Independent protected candidate or retained validation changed')
        self._assert_selection()
        _require(type(validation) is _CompileValidation
                 and validation.selection is self._selection
                 and validation.root == self._tx.root and validation.target == self.target,
                 'Independent validation source selection or session changed')
        _verify_selected_proof(validation.proof, validation.replay.final_tree,
                               validation.target, self._selection.source_profile)
        _require(validation.proof.source_profile == self._selection.source_profile
                 and validation.proof.grammar_version == self._selection.grammar_version,
                 'Independent validation has a different source profile or grammar')
        self._assert_selection()

    def _analyze_source(self, tree, catalog):
        self._assert_selection()
        try:
            return _script(tree, self.target, catalog,
                           source_profile=self._selection.source_profile)
        except ClosedCoreError as error:
            if tree.source == self._baseline:
                raise
            raise ProtectedCoreError(f'Protected candidate left its selected grammar: {error}') from error

    def _analyze_graph(self, graph):
        _require(self._active, 'Active protected core context required')
        catalog = self._context(graph)
        tree = parse(graph.sources[self.target])
        result, findings = self._analyze_source(tree, catalog)
        _require(not result.edits or tree.source == self._baseline,
                 'Protected core refuses a second repair pass')
        analysis = GraphAnalysis(CORE_RULE_IDS, CORE_RULE_IDS,
            frozenset((self.target,)), self._evidence_paths,
            findings=findings,
            edits=tuple(SourceEdit(self.target, edit) for edit in result.edits))
        self._history.append(_Pass(tree, result, analysis))
        return analysis

    def _independent_replay(self, snapshot):
        catalog = self._context(snapshot)
        tree = parse(self._baseline)
        initial, _ = self._analyze_source(tree, catalog)

        def analyze(checked):
            return {SCRIPT: self._analyze_source(checked, catalog)[0]}

        def step(before, old, after, new, history):
            _require(not history.deferred and not history.moves,
                     'Protected script pass has conflicts or source movement')
            _require(_attachment_roles(before) == _attachment_roles(after),
                     'Script repair changed semantic attachment identities')
            _require(_script_ownership(before) == _script_ownership(after),
                     'Script repair changed wrapper, equation or nested operand ownership')
            _require(history.edits == plan(before.source, old[SCRIPT].edits).edits
                     and history.after == plan(before.source, old[SCRIPT].edits).candidate(),
                     'Independent script replay differs from exact raw proposals')
            _require(all(e.start == e.end and e.replacement
                         and not e.replacement.strip(b'()') for e in history.edits),
                     'Protected script repair must preserve every original source byte')
            _require(not (new[SCRIPT].diagnostics or new[SCRIPT].edits),
                     'Protected core requires zero findings after one pass')

        def final(candidate):
            _require(candidate.passes <= 1 and not candidate.final_results[SCRIPT].diagnostics
                     and not candidate.final_results[SCRIPT].edits,
                     'Protected core candidate is not a clean one-pass result')
            _require(all(item.primary_rule_id == SCRIPT and item.rule_ids == (SCRIPT,)
                         and item.contributions
                         and all(c.rule_id == SCRIPT and c.pass_number == 1
                                 for c in item.contributions) for item in candidate.edits),
                     'Independent candidate has unowned repair attribution')
            _require(plan(candidate.baseline, (e.edit for e in candidate.edits)).candidate()
                     == candidate.content, 'Independent original-source replay mismatch')

        candidate = converge(tree, {SCRIPT: initial}, analyze, step, final, max_passes=1)
        final(candidate)
        return candidate

    def validate(self):
        self._assert_selection()
        _require(self._active, 'Active protected core context required')
        self._candidate = None
        self._history = []
        self._validated_summary = None
        self._summary = None
        self._compile_validation = None
        self._validation_authority = None
        self._applied_validation = None
        self._compile_snapshot = None
        self._compile_receipt = None
        snapshot = self._tx.snapshot
        token = self._tx.validate(self._analyze_graph, required_rules=CORE_RULE_IDS)
        _require(not token.findings and token.passes <= 1,
                 'Protected core requires a clean one-pass transaction candidate')
        replay = self._independent_replay(snapshot)
        _require(token.sources == {self.target: replay.content}
                 and token.passes == replay.passes,
                 'Transaction and independent provenance candidate disagree')
        expected_sources = ((self._baseline, replay.content, replay.content)
                            if token.passes else (self._baseline, self._baseline))
        _require(tuple(p.tree.source for p in self._history) == expected_sources,
                 'Transaction analysis pass history differs from independent replay')
        _require(self._history[0].result == replay.initial_results[SCRIPT]
                 and all(p.result == replay.final_results[SCRIPT]
                         and not p.analysis.findings for p in self._history[1:]),
                 'Transaction attribution/results differ from independent analysis')
        if replay.steps:
            _require(tuple(e.edit for e in self._history[0].analysis.edits)
                     == replay.steps[0].edits,
                     'Transaction edit attribution differs from independent pass')
        # Independent replay is outside the analysis callback; recheck the live
        # captured identities/bytes again before blessing this bridge token.
        self._tx._revalidate()
        self._assert_selection()
        try:
            proof = certify(replay.final_tree, self.target,
                            source_profile=self._selection.source_profile)
        except ClosedCoreError as error:
            raise ProtectedCoreError(f'Retained final admission changed: {error}') from error
        _verify_selected_proof(proof, replay.final_tree, self.target, self._selection.source_profile)
        self._validated_summary = ProtectedCoreSummary(
            self._selection.source_profile, self.target, digest(self._baseline), digest(replay.content),
            tuple(sorted(CORE_RULE_IDS)), tuple(proof.evaluated_rule_ids),
            tuple(proof.structural_reasons),
            tuple(AppliedInsertion(self.target, item.edit.start, item.edit.end,
                                   item.edit.replacement) for item in replay.edits),
            grammar_version=self._selection.grammar_version)
        self._compile_validation = _CompileValidation(token, replay, proof,
            snapshot.root, self.target, snapshot.evidence,
            digest(snapshot.evidence[self._config_path]),
            digest(snapshot.evidence[self._catalog_path]), self._selection)
        self._validation_authority = (self._compile_validation, token.sources,
                                     _retain_authority(self._compile_validation))
        self._candidate = token
        return token

    def apply(self, candidate):
        _require(self._active and candidate is not None and candidate is self._candidate,
                 'Only this bridge session\'s independently validated candidate may be applied')
        validation = self._compile_validation
        _require(validation is not None and getattr(validation, 'candidate', None) is candidate,
                 'Independent protected candidate provenance required')
        self._assert_validation(validation)
        self._candidate = None
        result = self._tx.apply(candidate)
        # Only this bridge's successful actual apply establishes authority. A
        # transaction receipt obtained through the inspection property does not.
        self._applied_validation = validation
        # This reporting-only value is never an input to token validation/apply.
        self._summary = replace(self._validated_summary, modified_paths=result.modified,
                                journal_id=result.journal)
        # This check is after successful actual commit, outside transaction
        # rollback. A later selection/proof drift refuses authority while the
        # original applied summary and committed fixes remain available.
        self._assert_validation(validation)
        return result

    def _assert_compile_binding(self, snapshot):
        self._assert_selection()
        validation = self._applied_validation
        _require(self._active and validation is not None
                 and snapshot is not None and snapshot is self._compile_snapshot
                 and type(snapshot) is ClosedCoreCompileSnapshot
                 and snapshot._owner is self._compile_owner
                 and snapshot._validation is validation,
                 'Only this active bridge\'s independently applied candidate has a compile snapshot')
        self._assert_validation(validation)
        receipt, proof, replay = snapshot._receipt, validation.proof, validation.replay
        _require(type(receipt) is CommittedSnapshot and receipt is self._compile_receipt,
                 'Compile snapshot transaction receipt changed')
        # Recheck retained proof/tree binding only. No live-file parsing or
        # reanalysis can manufacture a new successful candidate at handoff.
        _require(receipt.root == snapshot.root == validation.root
                 and snapshot.target == validation.target
                 and frozenset(receipt.sources) == frozenset((validation.target,))
                 and snapshot.source is receipt.sources[validation.target]
                 and snapshot.source == validation.candidate.sources[validation.target] == replay.content
                 and proof.source_sha256 == snapshot.source_sha256 == digest(snapshot.source)
                 and proof.source_bytes == len(snapshot.source)
                 and snapshot.evidence is receipt.evidence
                 and snapshot.evidence == validation.evidence
                 and snapshot.required_rule_ids == tuple(sorted(CORE_RULE_IDS))
                 and snapshot.evaluated_rule_ids == tuple(proof.evaluated_rule_ids)
                 and snapshot.structural_reasons == tuple(proof.structural_reasons)
                 and snapshot.config_sha256 == validation.config_sha256
                 and snapshot.catalog_sha256 == validation.catalog_sha256,
                 'Compile snapshot differs from retained independent candidate/proof/evidence')
        _require(snapshot.source_profile == self._selection.source_profile
                 and snapshot.grammar_version == self._selection.grammar_version,
                 'Compile snapshot source profile or grammar changed')

    def compile_snapshot(self) -> ClosedCoreCompileSnapshot:
        """Guard and expose the exact postimage of this bridge's own apply.

        No compiler is invoked, no resources are captured and no artifact is
        published. Reporting summaries and bare transaction receipts are not
        accepted as authority. Post-commit refusal never rolls back valid fixes.
        """
        validation = self._applied_validation
        _require(self._active and validation is not None,
                 'A successful independently validated bridge apply is required for a compile snapshot')
        self._assert_validation(validation)
        receipt = self._tx.committed_snapshot()
        if self._compile_snapshot is None:
            self._compile_receipt = receipt
            snapshot = object.__new__(ClosedCoreCompileSnapshot)
            for name, value in dict(root=validation.root, target=validation.target,
                    source=receipt.sources[validation.target], source_sha256=validation.proof.source_sha256,
                    evidence=receipt.evidence, required_rule_ids=tuple(sorted(CORE_RULE_IDS)),
                    evaluated_rule_ids=tuple(validation.proof.evaluated_rule_ids),
                    structural_reasons=tuple(validation.proof.structural_reasons),
                    config_sha256=validation.config_sha256, catalog_sha256=validation.catalog_sha256,
                    source_profile=self._selection.source_profile,
                    grammar_version=self._selection.grammar_version,
                    _receipt=receipt, _validation=validation, _owner=self._compile_owner).items():
                object.__setattr__(snapshot, name, value)
            self._compile_snapshot = snapshot
        self._assert_compile_binding(self._compile_snapshot)
        return self._compile_snapshot

    def assert_compile_current(self, snapshot: ClosedCoreCompileSnapshot) -> None:
        """Refuse stale or foreign generations without touching committed work."""
        self._assert_compile_binding(snapshot)
        self._tx.assert_current(snapshot._receipt)
