"""Pure, internal, bounded text-utils paragraph applicability proof.

No parser execution, filesystem/catalog loads, writes, or transaction authority.
The bridge must retain genuine capture/parser authority and run the five actual
obligations. This module never converts ordinary PARTIAL analysis into coverage.
"""
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import json
import re
import tomllib
import unicodedata
import weakref

import closed_core
import captured_relative_core
from capitalization import WORDS, Context, StartKind, BodyKind, MarkerBinding
from static_resolver import PackageSource, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256, resolve
from syntax_client import Tree, Node, MAX_INPUT, MAX_NODES
from typst_lint import SafetyError, InputError, parse_rules

SOURCE_PROFILE = 'captured-text-utils-paragraph-v1'
GRAMMAR_VERSION = 'captured-text-utils-paragraph-grammar-v1'
# Internal admission only. Never accepted by the final compiler proof contract.
REPAIR_GRAMMAR_VERSION = 'captured-text-utils-paragraph-repair-grammar-v1'
MAX_REPAIR_PASSES = 2
MAX_REPAIR_EDITS = 512
PARSER_CONTRACT = closed_core.PARSER_CONTRACT
IMPORT_PATH = TEXT_UTILS
SOURCE_IDENTITY = 'text-utils/0.1.2/lib.typ'
MANIFEST_IDENTITY = 'text-utils/0.1.2/typst.toml'
SOURCE_SHA256 = TEXT_UTILS_SOURCE_SHA256
SOURCE_BYTES = 5583
MANIFEST_SHA256 = 'e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2'
MANIFEST_BYTES = 173
CATALOG_SHA256 = '955b0c721027369ec1fd100a7da00fc7304693bb40e2916904c6f3b06fae1826'
MAX_PARAGRAPHS = 256
MAX_CONFIG_BYTES = 65536
MAX_CATALOG_BYTES = 65536
EVALUATED_RULE_IDS = ('core.parenthesized-scripts', 'pde.paragraph-marker-gap',
    'pde.paragraph-marker-target', 'pde.paragraph-capitalization', 'pde.prose-paragraph-marker')
STRUCTURAL_REASONS = (
 ('core.scoped-function-call', 'No call, field, scope receiver or binding declaration occurs in the root; the sole reviewed package has no scope APIs.'),
 ('core.scoped-marker-tags', 'No annotation scope, mark call, label or tag argument occurs.'),
 ('core.scoped-anchor-access', 'No annotation drawing, marker declaration, anchor string call or drawing call occurs.'),
 ('core.scoped-reference-access', 'No reference, generated scoped tag/name or reference call occurs.'),
 ('core.local-overlay-api', 'No local scope, raw annot-cetz call or overlay application occurs.'),
 ('core.annotation-in-display', 'No annotation/overlay call occurs; every equation is explicitly inline.'),
 ('core.annotation-marker-bounds', 'No overlay, overlay target, scoped marker or label occurs.'),
 ('core.unique-scope-namespace', 'The exhaustive root and exact dependency-free package contain no scope creation or scope namespace API; no additional graph source exists.'),
 ('core.project-ref-arguments', 'No project-ref binding or call occurs in the root or reviewed package.'),
 ('core.project-ref-catalog-target', 'No project-ref call or project/label target occurs, regardless of external catalogs.'),
 ('core.single-book-show', 'The sole import is text-utils, not a math-book helper; its using-shared-math-book-helper precondition is false.'),
 ('core.no-duplicate-book-reset', 'The sole show applies paragraph tabs; no book owner or math-block-reset application occurs.'),
 ('core.no-set-theme', 'No root set rule or custom theme helper application occurs.'),
 ('core.theme-in-context', 'No root closure, reusable helper, theme binding or theme-resolution call occurs; unused reviewed package closures are never invoked.'),
 ('pde.import-order', 'There is exactly one named text-utils import; no Styles/styles.typ, local figure or annotation import ordering pair exists.'),
 ('pde.no-redundant-facade-import', 'No styles, mannot_utils or local_tags facade import occurs.'),
 ('pde.continuation-line', 'Every prose paragraph occupies exactly one physical source line; no adjacent prose sentences span source lines.'),
 ('pde.display-continuation-gap', 'Every admitted equation has typed display=False; no display/prose boundary exists.'),
 ('pde.proof-trailing-gap', 'No proof binding, proof call, content block or proof-ending boundary occurs.'),
 ('pde.footnote-trailing-gap', 'No footnote binding, call, content body or footnote-ending line occurs.'),
 ('pde.prose-footnote-punctuation', 'No prose footnote call occurs.'),
 ('pde.aligned-footnote-spacer', 'No display, alignment token, footnote call or dots_space binding occurs.'),
 ('pde.unaligned-footnote-no-spacer', 'No display, footnote call or dots_space binding occurs.'),
 ('pde.prose-footnote-no-spacer', 'No prose footnote or dots_space binding/use occurs.'),
 ('pde.standalone-display', 'Every admitted equation has typed display=False; no display delimiter occurs.'),
 ('pde.text-only-highlight', 'No highlight/highlighted binding is imported or called; the unused reviewed package export is not invoked.'),
 ('pde.theme-cancelmark', 'No math call, cancel call, cancelmark binding or facade occurs.'),
 ('pde.flowbox-colored-border', 'No flowbox implementation or flowbox closure/call/border/stroke argument occurs in the selected root or sole reviewed package.'),
)
NOT_APPLICABLE_RULE_IDS = tuple(r for r, _ in STRUCTURAL_REASONS)
RULE_IDS = EVALUATED_RULE_IDS + NOT_APPLICABLE_RULE_IDS
_ISSUER = object()
_REPAIR_ISSUER = object()
# Identity-only weak issuance tracking: copied frozen data confers no authority.
_REPAIR_PROOFS = weakref.WeakValueDictionary()
_HSPACE = re.compile(rb'[ \t]*\Z')
_WS = re.compile(rb'[ \t]*(?:\r?\n[ \t]*)*\Z')
_GAP = re.compile(rb'[ \t]*\r?\n[ \t]*\Z')

class CapturedTextUtilsParagraphError(SafetyError, InputError):
    pass
class CapturedTextUtilsParagraphSyntaxError(CapturedTextUtilsParagraphError):
    pass
class CapturedTextUtilsParagraphAuthorityError(CapturedTextUtilsParagraphError):
    pass

def _require(ok, message):
    if not ok:
        raise CapturedTextUtilsParagraphAuthorityError(message)
def _syntax(ok, message):
    if not ok:
        raise CapturedTextUtilsParagraphSyntaxError(message)
def _sha(raw):
    return hashlib.sha256(raw).hexdigest()

@dataclass(frozen=True, slots=True)
class ParagraphConfig:
    target_path: str
    entrypoint: str | None
    language: str
    source_sha256: str

def canonical_path(path):
    try:
        result = captured_relative_core.canonical_path(path)
        _require(not any(c in path for c in '*?[]{}'), 'Target must be literal, not a glob')
        return result
    except captured_relative_core.CapturedRelativeCoreError as error:
        raise CapturedTextUtilsParagraphAuthorityError(str(error)) from error

def validate_config(raw, target_path):
    canonical_path(target_path)
    _require(type(raw) is bytes and len(raw) <= MAX_CONFIG_BYTES, 'Bounded explicit config bytes required')
    try:
        data = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Invalid paragraph configuration') from error
    _require(type(data) is dict and {'rule-set','include','capitalization'} <= set(data)
        and set(data) <= {'rule-set','include','capitalization','entrypoint'}, 'Unsupported paragraph configuration keys')
    _require(type(data['rule-set']) is str and data['rule-set'] == 'pde', 'Explicit pde rule-set required')
    _require(type(data['include']) is list and len(data['include']) == 1
        and type(data['include'][0]) is str and data['include'][0] == target_path, 'Include must name exactly the canonical target')
    _require('entrypoint' not in data or (type(data['entrypoint']) is str and data['entrypoint'] == target_path), 'Entrypoint must match target')
    cap = data['capitalization']
    _require(type(cap) is dict and set(cap) == {'language'}
        and type(cap['language']) is str and cap['language'] == 'en', 'Only explicit capitalization.language=en is admitted')
    return ParagraphConfig(target_path, data.get('entrypoint'), 'en', _sha(raw))

def _catalog(raw):
    _require(type(raw) is bytes and len(raw) <= MAX_CATALOG_BYTES and _sha(raw) == CATALOG_SHA256, 'Exact reviewed full catalog required')
    try:
        data, _ = parse_rules(raw)
    except (ValueError, UnicodeError, TypeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Invalid reviewed catalog') from error
    ids = tuple(r['id'] for r in data['rules'])
    _require(len(ids) == 33 and len(set(RULE_IDS)) == 33 and set(ids) == set(RULE_IDS)
        and sum(i.startswith('core.') for i in ids) == 15
        and sum(i.startswith('pde.') for i in ids) == 18, 'Reviewed 15 core + 18 PDE rule matrix disagrees')
    return tuple((r['id'], _sha(json.dumps(r, sort_keys=True, separators=(',',':')).encode())) for r in data['rules'])

def _validated(tree, path):
    try:
        return captured_relative_core._validate_tree(tree, path)
    except captured_relative_core.CapturedRelativeSyntaxError as error:
        raise CapturedTextUtilsParagraphSyntaxError(str(error)) from error
    except (captured_relative_core.CapturedRelativeCoreError, AttributeError, TypeError, ValueError, KeyError, RuntimeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Invalid bounded immutable original Tree') from error

def _fingerprint(records):
    return _sha(json.dumps(records, sort_keys=True, separators=(',',':')).encode())

@dataclass(frozen=True, slots=True)
class PackageIdentity:
    import_path: str
    source_identity: str
    source_sha256: str
    source_bytes: int
    manifest_identity: str
    manifest_sha256: str
    manifest_bytes: int
    tree_sha256: str
    nodes: tuple[Node, ...]
    invoked_body_span: tuple[int, int]
    invoked_body_sha256: str
    exports: tuple[tuple[str, str], ...]

def _canonical_package_identity(value):
    return (len(value) <= 4096 and value.startswith('/') and value.endswith('/local/'+SOURCE_IDENTITY)
        and '\\' not in value and len(value.encode('utf-8')) <= 4096
        and all(p not in ('','.','..') for p in value.split('/')[1:])
        and not any(ord(c)<32 or ord(c)==127 for c in value))

def certify_package(package_source, manifest_bytes, package_tree):
    """Bind exact reviewed bytes/tree, separately from capture initializer proof.

    The invoked lib.typ:11-22 closure is reviewed independently: two fixed regex
    show rules inspect captured text, call upper/h(1.5em), and emit body. No file
    read, external operation or persistent mutation occurs. Unused exports are
    initializer evidence only and gain no call authority from this review.
    """
    from static_resolver import TEXT_UTILS_EXPORTS
    try:
        _require(type(package_source) is PackageSource and type(package_source.import_path) is str
            and package_source.import_path == IMPORT_PATH and type(package_source.source_identity) is str
            and _canonical_package_identity(package_source.source_identity) and type(package_source.source) is bytes
            and len(package_source.source) == SOURCE_BYTES and _sha(package_source.source) == SOURCE_SHA256
            and type(package_source.artifacts) is tuple and not package_source.artifacts,
            'Exact reviewed genuine PackageSource required')
        _require(type(manifest_bytes) is bytes and len(manifest_bytes) == MANIFEST_BYTES
            and _sha(manifest_bytes) == MANIFEST_SHA256, 'Exact reviewed text-utils manifest required')
        metadata = tomllib.loads(manifest_bytes.decode())['package']
        _require((metadata['name'],metadata['version'],metadata['entrypoint']) == ('text-utils','0.1.2','lib.typ'), 'Package manifest metadata changed')
        records = _validated(package_tree, 'text-utils/0.1.2/lib.typ')
        _require(package_tree.source == package_source.source, 'Package Tree/source mismatch')
        lines = package_source.source.splitlines(keepends=True)
        start, end = sum(map(len, lines[:10])), sum(map(len, lines[:22]))
        invoked = package_source.source[start:end]
        _require(invoked.startswith(b'#let apply-paragraph-tabs(body) = {\n') and invoked.endswith(b'  body\n}\n'), 'Reviewed call body changed')
        return PackageIdentity(IMPORT_PATH,package_source.source_identity,SOURCE_SHA256,SOURCE_BYTES,
            MANIFEST_IDENTITY,MANIFEST_SHA256,MANIFEST_BYTES,_fingerprint(records),package_tree.nodes,
            (start,end),_sha(invoked),TEXT_UTILS_EXPORTS)
    except CapturedTextUtilsParagraphError:
        raise
    except (AttributeError, TypeError, KeyError, ValueError, UnicodeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Malformed reviewed package evidence') from error

@dataclass(frozen=True, slots=True)
class Statement:
    hash_node: int
    node: int
    span: tuple[int,int]
    bytes: bytes
    bound_nodes: tuple[int,...]
@dataclass(frozen=True, slots=True)
class Paragraph:
    ordinal: int
    span: tuple[int,int]
    content_nodes: tuple[int,...]
    first_node: int
    first_span: tuple[int,int]
    first_word: bytes
    marker_nodes: tuple[int,int] | None
    marker_span: tuple[int,int] | None
    marker_gap_span: tuple[int,int] | None
    edit_start: int | None
    replacement: bytes
@dataclass(frozen=True, slots=True)
class Boundary:
    ordinal: int
    previous_paragraph: int
    next_paragraph: int
    gap_span: tuple[int,int]
    gap_bytes: bytes
    marker_nodes: tuple[int,int]
    marker_span: tuple[int,int]
    first_node: int
@dataclass(frozen=True, slots=True)
class RuleObligation:
    rule_id: str
    catalog_record_sha256: str
    disposition: str
    reason: str
    source_sha256: str
    tree_sha256: str
@dataclass(frozen=True, slots=True, init=False)
class CapturedTextUtilsParagraphProof:
    source_profile: str
    grammar_version: str
    parser_contract: tuple[str,...]
    target_path: str
    source_sha256: str
    source_bytes: int
    tree_sha256: str
    nodes: tuple[Node,...]
    config: ParagraphConfig
    catalog_sha256: str
    package: PackageIdentity
    import_statement: Statement
    show_statement: Statement
    paragraphs: tuple[Paragraph,...]
    boundaries: tuple[Boundary,...]
    contexts: tuple[Context,...]
    evaluated_rule_ids: tuple[str,...]
    not_applicable_rule_ids: tuple[str,...]
    structural_reasons: tuple[tuple[str,str],...]
    obligations: tuple[RuleObligation,...]
    _issuer: object = field(repr=False, compare=False)
    def __init__(self, *args, **kwargs):
        raise TypeError('Paragraph proof is privately issued by certify')

@dataclass(frozen=True, slots=True)
class Repair:
    rule_id: str
    paragraph_ordinal: int
    start: int
    end: int
    replacement: bytes

@dataclass(frozen=True, slots=True)
class RepairBoundary:
    ordinal: int
    previous_paragraph: int
    next_paragraph: int
    gap_span: tuple[int,int]
    gap_bytes: bytes
    marker_nodes: tuple[int,int] | None
    marker_span: tuple[int,int] | None
    first_node: int

@dataclass(frozen=True, slots=True, weakref_slot=True, init=False)
class CapturedTextUtilsParagraphRepairProof(CapturedTextUtilsParagraphProof):
    repairs: tuple[Repair,...]
    def __init__(self, *args, **kwargs):
        raise TypeError('Repair admission is privately issued by certify_repair')


class _Grammar(closed_core._Grammar):
    def __init__(self, tree, path, package, *, repair=False):
        super().__init__(tree, 'structured-core-grammar-v1')
        self.path, self.package = path, package
        self.repair, self.repairs = repair, []
        self.paragraphs, self.boundaries = [], []
        self.ids = tree.nodes[0].children
        self.position = 0
    def ws(self, i):
        n = self.nodes[i]
        text = self.token(i,n.kind)
        _syntax(n.kind in ('Space','Parbreak') and _WS.fullmatch(text.encode()) is not None, 'Only literal LF/CRLF whitespace is admitted')
    def trivia(self):
        start=self.position
        while self.position < len(self.ids) and self.nodes[self.ids[self.position]].kind in ('Space','Parbreak'):
            self.ws(self.ids[self.position]); self.position += 1
        return self.ids[start:self.position]
    def standalone(self, start, end):
        raw = self.tree.source
        line = raw.rfind(b'\n',0,start)+1
        if line == 0 and raw.startswith(b'\xef\xbb\xbf'):
            line = 3
        _syntax(_HSPACE.fullmatch(raw[line:start]) is not None, 'Statement/marker must begin on its own physical line')
        finish = raw.find(b'\n',end)
        suffix = raw[end:finish] if finish >= 0 else raw[end:]
        if finish >= 0 and suffix.endswith(b'\r'): suffix = suffix[:-1]
        _syntax(_HSPACE.fullmatch(suffix) is not None, 'Statement/marker must end on its own physical line')
    def has_blank_before(self, start):
        raw = self.tree.source
        line = raw.rfind(b'\n',0,start)+1
        previous = raw.rfind(b'\n',0,max(0,line-1))+1
        gap = raw[previous:line]
        return gap.endswith(b'\n') and _WS.fullmatch(gap) is not None and gap.count(b'\n') == 1
    def blank_before(self, start):
        _syntax(self.has_blank_before(start), 'Marker requires an immediately preceding terminated whitespace-only blank line')
    def gap_repair(self, marker_span):
        raw = self.tree.source
        line = raw.rfind(b'\n', 0, marker_span[0]) + 1
        previous = raw.rfind(b'\n', 0, max(0, line - 1)) + 1
        end = raw.find(b'\n', marker_span[1])
        suffix = raw[marker_span[1]:end] if end >= 0 else raw[marker_span[1]:]
        newline = b'\r\n' if (end >= 0 and suffix.endswith(b'\r') or raw[previous:line].endswith(b'\r\n')) else b'\n'
        self.repairs.append(Repair('pde.paragraph-marker-gap', len(self.paragraphs), line, line, newline))
    def parts(self, i, kind):
        n = self.enter(i)
        _syntax(n.kind == kind and bool(n.children), f'Expected exhaustive {kind}')
        result = []
        for child in n.children:
            if self.nodes[child].kind == 'Space':
                _syntax(_HSPACE.fullmatch(self.raw(child).encode()) is not None, 'Statements must be single-line')
                self.space(child)
            else: result.append(child)
        return tuple(result)
    def statement(self, kind):
        _syntax(self.position+1 < len(self.ids), f'Missing {kind}')
        h,i = self.ids[self.position:self.position+2]
        self.token(h,'Hash','#')
        _syntax(self.nodes[h].end == self.nodes[i].start, 'Hash must be adjacent to statement')
        self.standalone(self.nodes[h].start,self.nodes[i].end)
        parts = self.parts(i,kind)
        bound = ()
        if kind == 'ModuleImport':
            _syntax(len(parts) == 4,'Only one exact direct named package import is admitted')
            self.token(parts[0],'Import','import'); self.token(parts[1],'Str','"'+IMPORT_PATH+'"'); self.token(parts[2],'Colon',':')
            items = self.parts(parts[3],'ImportItems')
            _syntax(len(items) == 3,'Exactly paragraph_tab then apply-paragraph-tabs must be imported')
            names=[]
            for j,name in ((items[0],'paragraph_tab'),(items[2],'apply-paragraph-tabs')):
                item = self.parts(j,'ImportItemPath')
                _syntax(len(item) == 1,'No import alias/field path is admitted')
                self.token(item[0],'Ident',name); names.append(item[0])
            self.token(items[1],'Comma',','); bound=tuple(names)
        else:
            _syntax(len(parts)==3,'Exactly one direct root show is required')
            self.token(parts[0],'Show','show'); self.token(parts[1],'Colon',':'); self.token(parts[2],'Ident','apply-paragraph-tabs'); bound=(parts[2],)
        self.position += 2
        start,end=self.nodes[h].start,self.nodes[i].end
        return Statement(h,i,(start,end),self.tree.source[start:end],bound)
    def text(self, i):
        text=self.token(i,'Text')
        _syntax(bool(text) and '\n' not in text and '\r' not in text and all(
            c.isprintable() and c not in closed_core._PROSE_META and unicodedata.category(c) != 'Co' for c in text), 'Only literal nonprivate single-line prose is admitted')
    def run(self):
        self.enter(0)
        if self.ids and self.nodes[self.ids[0]].kind=='Text' and self.raw(self.ids[0])=='\ufeff':
            self.token(self.ids[0],'Text','\ufeff'); self.position=1
        self.trivia(); imported=self.statement('ModuleImport')
        before=self.position; self.trivia()
        _syntax(self.position>before and b'\n' in self.tree.source[imported.span[1]:self.nodes[self.ids[self.position]].start] if self.position<len(self.ids) else False, 'Import and show require separate physical lines')
        shown=self.statement('ShowRule')
        while self.position<len(self.ids):
            separators=self.trivia()
            if self.position == len(self.ids): break
            _require(len(self.paragraphs) < MAX_PARAGRAPHS, 'Paragraph count budget exceeded')
            start_id=self.ids[self.position]; marker_nodes=marker_span=marker_gap=None
            if self.nodes[start_id].kind=='Hash':
                _syntax(self.position+2<len(self.ids),'Incomplete paragraph marker')
                h,i=self.ids[self.position:self.position+2]
                self.token(h,'Hash','#'); self.token(i,'Ident','paragraph_tab')
                _syntax(self.nodes[h].end==self.nodes[i].start,'Marker hash must be adjacent')
                marker_nodes=(h,i); marker_span=(self.nodes[h].start,self.nodes[i].end)
                self.standalone(*marker_span)
                if self.repair and not self.has_blank_before(marker_span[0]): self.gap_repair(marker_span)
                else: self.blank_before(marker_span[0])
                self.position+=2
                gap=self.ids[self.position]
                _syntax(self.nodes[gap].kind=='Space' and _GAP.fullmatch(self.raw(gap).encode()) is not None,'Marker must be adjacent to first prose line by exactly one LF/CRLF')
                self.ws(gap); self.position+=1
            _syntax(self.position<len(self.ids),'Marker has no paragraph')
            first_id=self.ids[self.position]; first=self.nodes[first_id]
            _syntax(first.kind=='Text','Paragraph must start with direct literal Text')
            _syntax(self.repair or marker_nodes is not None or not self.paragraphs,'Every after-existing-prose boundary requires its own canonical marker')
            if marker_span: marker_gap=(marker_span[1],first.start)
            else:
                _syntax(b'\n' in self.tree.source[shown.span[1]:first.start], 'Show must precede prose on a separate physical line')
            content=[]; start=first.start; end=start
            while self.position<len(self.ids):
                i=self.ids[self.position]; n=self.nodes[i]
                if n.kind in ('Space','Parbreak') and b'\n' in self.tree.source[n.start:n.end]: break
                if n.kind=='Text': self.text(i)
                elif n.kind=='Space': self.ws(i)
                elif n.kind=='Equation':
                    _syntax(n.display is False and b'\n' not in self.tree.source[n.start:n.end] and b'\r' not in self.tree.source[n.start:n.end], 'Only single-line inline math is admitted')
                    self.equation(i)
                else: raise CapturedTextUtilsParagraphSyntaxError(f'Unsupported paragraph token {n.kind}')
                content.append(i); end=n.end; self.position+=1
            word=b''; word_end=first.start; edit=None; replacement=b''
            if marker_nodes or (self.repair and self.paragraphs):
                match=re.match(rb'[A-Za-z]+',self.tree.source[first.start:first.end])
                _syntax(match is not None,'Marked starter must be an ASCII allowlisted word')
                word=match[0]; word_end=first.start+len(word)
                _syntax(word.lower() in WORDS and word_end<len(self.tree.source)
                    and self.tree.source[word_end:word_end+1] in (b' ',b'\t',b'\r',b'\n'), 'Marked starter must be a complete allowlisted ASCII word followed by whitespace')
                clean=word==word[:1].upper()+word[1:].lower() or word.isupper()
                _syntax(clean or word.islower(),'Mixed-case marked starter is unsupported')
                if marker_nodes and not clean: edit=first.start; replacement=word[:1].upper()
            p=Paragraph(len(self.paragraphs),(start,end),tuple(content),first_id,(first.start,word_end),word,marker_nodes,marker_span,marker_gap,edit,replacement)
            if self.paragraphs:
                previous=self.paragraphs[-1]
                gap=(previous.span[1],marker_span[0] if marker_span else first.start)
                gap_raw=self.tree.source[gap[0]:gap[1]]
                _syntax(_WS.fullmatch(gap_raw) is not None and gap_raw.count(b'\n') >= (1 if self.repair and marker_span else 2),'After-prose marker needs a blank source boundary')
                if self.repair and marker_span is None:
                    # The full AST walk already consumed the whitespace-only
                    # boundary. Its parser-owned Parbreak, not prose text,
                    # establishes the author's existing paragraph separation.
                    _syntax(any(self.nodes[i].kind=='Parbreak' and gap[0] <= self.nodes[i].start < self.nodes[i].end <= gap[1] for i in separators), 'Missing marker requires an existing parser-owned Parbreak')
                    at=self.tree.source.rfind(b'\n',gap[0],first.start)+1
                    newline=b'\r\n' if self.tree.source[at-2:at]==b'\r\n' else b'\n'
                    self.repairs.append(Repair('pde.prose-paragraph-marker',p.ordinal,at,at,b'#paragraph_tab'+newline))
                cls=RepairBoundary if self.repair else Boundary
                self.boundaries.append(cls(len(self.boundaries),previous.ordinal,p.ordinal,gap,gap_raw,marker_nodes,marker_span,first_id))
            self.paragraphs.append(p)
        _syntax(any(p.marker_nodes is not None for p in self.paragraphs) or (self.repair and bool(self.repairs)),'At least one marked or repairable literal prose paragraph is required')
        _syntax(len(self.visited)==len(self.nodes),'Every original AST descendant/token must be consumed')
        for n in self.nodes:
            for _,operand in n.operands:
                o=self.nodes[operand]
                _syntax(o.kind=='Math' and bool(o.children) and self.nodes[o.children[0]].kind=='LeftParen' and self.nodes[o.children[-1]].kind=='RightParen','Inline scripts must already be clean and parenthesized')
        resolution=resolve(self.tree,fallback_language='en',packages=(self.package,))
        _syntax(not resolution.skips,'Exact root resolution must have no skipped constructs')
        for i,name in (*((p.marker_nodes[1],'paragraph_tab') for p in self.paragraphs if p.marker_nodes), (shown.bound_nodes[0],'apply-paragraph-tabs')):
            binding=resolution.binding_for(i)
            _syntax(binding is not None and binding.kind=='api' and binding.name==name and binding.package==IMPORT_PATH
                and binding.source_identity==self.package.source_identity and binding.source_sha256==SOURCE_SHA256,'Imported helper identity must resolve to reviewed package')
        for p in self.paragraphs:
            if self.repair and p.ordinal and p.marker_nodes is None:
                _syntax(p.first_node in resolution.paragraph_insertion_proof.available,'Canonical marker and active show availability must be independently resolved')
            facts=resolution.facts[p.first_node]
            _syntax(facts.prose_safe and not facts.deferred and facts.effective_language=='en','Known root prose/language ownership required')
        return imported,shown,tuple(self.paragraphs),tuple(self.boundaries)

def _certify(tree, path, *, config_bytes, catalog_bytes, package_source, manifest_bytes, package_tree, source_profile=SOURCE_PROFILE, repair=False):
    _require(type(source_profile) is str and source_profile==SOURCE_PROFILE,'Explicit internal paragraph profile required')
    config=validate_config(config_bytes,path); catalog=_catalog(catalog_bytes)
    records=_validated(tree,path); package=certify_package(package_source,manifest_bytes,package_tree)
    try:
        grammar=_Grammar(tree,path,package_source,repair=repair)
        imported,shown,paragraphs,boundaries=grammar.run()
    except CapturedTextUtilsParagraphError: raise
    except closed_core.ClosedCoreError as error:
        raise CapturedTextUtilsParagraphSyntaxError(str(error)) from error
    except (AttributeError,TypeError,ValueError,KeyError,IndexError,RuntimeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Malformed paragraph representation') from error
    sha,fingerprint=_sha(tree.source),_fingerprint(records)
    contexts=tuple(Context(sha,path,3 if tree.source.startswith(b'\xef\xbb\xbf') else 0,len(tree.source),
        p.marker_span[0],p.marker_span[1],p.first_span[0],p.first_span[1],p.first_word,
        StartKind.LITERAL_WORD,BodyKind.ORDINARY_PROSE,MarkerBinding.PARAGRAPH_TAB,'en')
        for p in paragraphs if p.marker_nodes is not None)
    reasons=dict(STRUCTURAL_REASONS)
    actual={
      'core.parenthesized-scripts':'Later actual script analyzer must confirm no diagnostics, edits, skips, parse errors or suppression.',
      'pde.paragraph-marker-gap':'Later actual marker-gap analyzer must confirm every marker has its preceding blank line.',
      'pde.paragraph-marker-target':'Later actual marker-target analyzer must confirm every marker targets direct prose, never a heading/display.',
      'pde.paragraph-capitalization':'Later actual capitalization analyzer alone may propose the recorded first-byte edits; final result must be clean.',
      'pde.prose-paragraph-marker':'Later independently invoked exhaustive bounded boundary checker must confirm every after-existing-prose boundary has its immediately associated marker.',
    }
    if repair:
        actual.update({
          'pde.paragraph-marker-gap':'Actual bounded marker-gap evaluation must propose exactly the recorded missing-blank insertions on this admitted source; the final actual result must be clean.',
          'pde.paragraph-capitalization':'Actual capitalization evaluation alone may propose recorded first-byte edits for existing markers; newly inserted-marker casing requires a later separately attributed pass and final clean evaluation.',
          'pde.prose-paragraph-marker':'Actual exhaustive bounded prose-marker evaluation must independently derive every existing paragraph boundary and propose exactly the recorded missing-marker insertions; final actual evaluation must confirm every required marker is present.',
        })
    obligations=tuple(RuleObligation(r,h,'requires-actual-evaluation' if r in actual else 'structurally-inapplicable',actual.get(r,reasons.get(r)),sha,fingerprint) for r,h in catalog)
    values=dict(source_profile=SOURCE_PROFILE,grammar_version=REPAIR_GRAMMAR_VERSION if repair else GRAMMAR_VERSION,parser_contract=PARSER_CONTRACT,target_path=path,
        source_sha256=sha,source_bytes=len(tree.source),tree_sha256=fingerprint,nodes=tree.nodes,config=config,catalog_sha256=CATALOG_SHA256,
        package=package,import_statement=imported,show_statement=shown,paragraphs=paragraphs,boundaries=boundaries,contexts=contexts,
        evaluated_rule_ids=EVALUATED_RULE_IDS,not_applicable_rule_ids=NOT_APPLICABLE_RULE_IDS,structural_reasons=STRUCTURAL_REASONS,obligations=obligations,_issuer=_REPAIR_ISSUER if repair else _ISSUER)
    if repair: values['repairs']=tuple(sorted(grammar.repairs,key=lambda r:(r.start,r.end,r.rule_id)))
    proof=object.__new__(CapturedTextUtilsParagraphRepairProof if repair else CapturedTextUtilsParagraphProof)
    for name,value in values.items(): object.__setattr__(proof,name,value)
    if repair: _REPAIR_PROOFS[id(proof)]=proof
    return proof

def certify(tree, path, **authority):
    """Unchanged clean structural grammar, including its compiler proof type."""
    _require('repair' not in authority, 'Clean grammar is not caller configurable')
    return _certify(tree,path,**authority)

def certify_repair(tree, path, **authority):
    """Admit only canonical paragraphs or independently determined insertions.

    This separate proof is never a final compiler receipt or global coverage.
    Original unmarked document-start prose remains exempt from marker/casing.
    """
    _require('repair' not in authority, 'Repair grammar is not caller configurable')
    return _certify(tree,path,repair=True,**authority)


def _same_exact(left,right):
    if type(left) is not type(right): return False
    if type(right) is tuple:
        return len(left)==len(right) and all(_same_exact(a,b) for a,b in zip(left,right))
    if is_dataclass(right):
        try: return all(_same_exact(getattr(left,f.name),getattr(right,f.name)) for f in fields(right))
        except AttributeError: return False
    return left==right

def verify_proof(proof, tree, path, **authority):
    _require(type(proof) is CapturedTextUtilsParagraphProof and getattr(proof,'_issuer',None) is _ISSUER,'Unissued paragraph proof')
    try: expected=certify(tree,path,**authority)
    except CapturedTextUtilsParagraphError as error:
        raise CapturedTextUtilsParagraphAuthorityError('Paragraph proof authority changed: '+str(error)) from error
    _require(_same_exact(proof,expected),'Paragraph proof differs from explicit selected source/tree/config/package/catalog')

def check_boundaries(tree, path, **authority):
    """Actual exhaustive bounded boundary obligation, separate from generic analyzer.

    Rewalks every root/descendant token, derives every prose paragraph and checks
    every after-existing-prose boundary. Never filters a generic partial skip.
    Returns source-bound boundary records, not an ordinary COMPLETE status.
    """
    return certify(tree,path,**authority).boundaries

def compare_candidate(before, after, *, before_tree, after_tree, path, **authority):
    """Verify both proofs and exact one-byte capitalization-only candidate."""
    verify_proof(before,before_tree,path,**authority); verify_proof(after,after_tree,path,**authority)
    _require(_same_exact(before.nodes,after.nodes),'Candidate changed AST kinds/spans/edges/operands/display metadata')
    expected=bytearray(before_tree.source)
    for p in before.paragraphs:
        if p.edit_start is not None: expected[p.edit_start:p.edit_start+1]=p.replacement
    _require(bytes(expected)==after_tree.source,'Candidate differs beyond exact recorded first ASCII bytes')
    _require(all(p.edit_start is None for p in after.paragraphs),'Candidate has remaining capitalization edits')
    _require(_same_exact(before.import_statement,after.import_statement) and _same_exact(before.show_statement,after.show_statement)
        and _same_exact(before.boundaries,after.boundaries) and _same_exact(before.package,after.package),'Candidate changed statement/boundary/package ownership')

certify_captured_text_utils_paragraph = certify
verify_captured_text_utils_paragraph = verify_proof

@dataclass(frozen=True, slots=True)
class BoundaryResult:
    source_sha256: str
    boundaries: tuple[Boundary,...]
    diagnostics: tuple = ()
    edits: tuple = ()
    skips: tuple = ()
    parse_errors: tuple = ()
    suppressed: tuple = ()

def analyze_boundaries(tree, path, **authority):
    """Actually execute the exhaustive bounded prose-boundary checker."""
    proof=certify(tree,path,**authority)
    return BoundaryResult(proof.source_sha256,proof.boundaries)

def verify_repair_proof(proof, tree, path, **authority):
    _require(type(proof) is CapturedTextUtilsParagraphRepairProof
        and getattr(proof,'_issuer',None) is _REPAIR_ISSUER and _REPAIR_PROOFS.get(id(proof)) is proof,
        'Unissued or copied paragraph repair admission')
    try: expected=certify_repair(tree,path,**authority)
    except CapturedTextUtilsParagraphError as error:
        raise CapturedTextUtilsParagraphAuthorityError('Repair admission authority changed: '+str(error)) from error
    _require(_same_exact(proof,expected), 'Repair admission differs from explicit selected authority')

def _verify_admission(proof, tree, path, authority):
    if type(proof) is CapturedTextUtilsParagraphRepairProof:
        verify_repair_proof(proof,tree,path,**authority)
    else:
        verify_proof(proof,tree,path,**authority)

def _pending(proof, *, final=False):
    repairs=list(proof.repairs) if type(proof) is CapturedTextUtilsParagraphRepairProof else []
    for p in proof.paragraphs:
        if p.edit_start is not None:
            repairs.append(Repair('pde.paragraph-capitalization',p.ordinal,p.edit_start,p.edit_start+1,p.replacement))
        elif final and p.ordinal and p.marker_nodes is None and p.first_word.islower():
            repairs.append(Repair('pde.paragraph-capitalization',p.ordinal,p.first_span[0],p.first_span[0]+1,p.first_word[:1].upper()))
    _require(len(repairs)<=MAX_REPAIR_EDITS, 'Repair edit budget exceeded')
    return tuple(sorted(repairs,key=lambda r:(r.start,r.end,r.rule_id)))

def _replay_repairs(raw, repairs):
    cursor=0; pieces=[]
    for r in repairs:
        _require(cursor<=r.start<=r.end<=len(raw), 'Overlapping paragraph repair ownership')
        pieces.extend((raw[cursor:r.start],r.replacement)); cursor=r.end
    pieces.append(raw[cursor:])
    result=b''.join(pieces)
    _require(len(result)<=MAX_INPUT, 'Paragraph repair exceeds root byte budget')
    return result

def _mapped(position, repairs, *, end=False):
    # Only independently derived insertions shift surviving original bytes.
    # Starts have right affinity; ends exclude insertions at that endpoint.
    return position+sum(len(r.replacement) for r in repairs if r.start==r.end
                        and (r.start<position or r.start==position and not end))

def _bind_original_nodes(before, after, before_tree, after_tree, repairs):
    """Bind every statement, marker, prose and math node by semantic ownership.

    Root trivia can split/merge when canonical markers are inserted. Exact byte
    replay preserves that trivia; exhaustive admission proves all new syntax.
    No caller-supplied map, text search or equal-looking copy proves survival.
    """
    _require(_same_exact(before.package,after.package), 'Repair changed package identity')
    _require(len(before.paragraphs)==len(after.paragraphs), 'Repair changed paragraph ownership')
    pairs=[(before.import_statement.hash_node,after.import_statement.hash_node),
           (before.import_statement.node,after.import_statement.node),
           (before.show_statement.hash_node,after.show_statement.hash_node),
           (before.show_statement.node,after.show_statement.node)]
    for old,new in zip(before.paragraphs,after.paragraphs):
        _require(old.ordinal==new.ordinal and len(old.content_nodes)==len(new.content_nodes), 'Repair changed paragraph content ownership')
        _require(tuple(_mapped(p,repairs,end=i==1) for i,p in enumerate(old.span))==new.span,
            'Repair changed paragraph extent')
        pairs.extend(zip(old.content_nodes,new.content_nodes))
        if old.marker_nodes is not None:
            _require(new.marker_nodes is not None, 'Repair removed an original marker')
            pairs.extend(zip(old.marker_nodes,new.marker_nodes))
        else:
            inserted=any(r.paragraph_ordinal==old.ordinal and r.rule_id=='pde.prose-paragraph-marker' for r in repairs)
            _require((new.marker_nodes is not None)==inserted, 'Repair forged a marker owner')
            if inserted:
                r=next(r for r in repairs if r.paragraph_ordinal==old.ordinal and r.rule_id=='pde.prose-paragraph-marker')
                at=_mapped(r.start,repairs,end=True)
                _require(new.marker_span==(at,at+len(b'#paragraph_tab')), 'Inserted canonical marker has wrong owner/span')
        if old.first_word:
            start=_mapped(old.first_span[0],repairs); end=_mapped(old.first_span[1],repairs,end=True)
            _require(new.first_span==(start,end), 'Repair changed first-word ownership')
    node_map={}; reverse=set(); stack=list(pairs)
    while stack:
        old_id,new_id=stack.pop()
        _require(old_id not in node_map and new_id not in reverse, 'Duplicated original/target node claim')
        old,new=before_tree.nodes[old_id],after_tree.nodes[new_id]
        _require(old.kind==new.kind and old.errors is new.errors and old.display is new.display
            and len(old.children)==len(new.children) and len(old.operands)==len(new.operands)
            and (_mapped(old.start,repairs),_mapped(old.end,repairs,end=True))==(new.start,new.end),
            'Repair changed original AST subtree shape, metadata or mapped span')
        node_map[old_id]=new_id; reverse.add(new_id)
        _require(len(node_map)<=MAX_NODES, 'Original-node map budget exceeded')
        stack.extend(zip(old.children,new.children))
    for old_id,new_id in node_map.items():
        old,new=before_tree.nodes[old_id],after_tree.nodes[new_id]
        _require(tuple((role,node_map.get(operand)) for role,operand in old.operands)==new.operands,
            'Repair changed math operand roles/owners')
    return tuple(sorted(node_map.items()))

def _check_step_record(step, repairs, before_raw, after_raw, *, pass_number=None):
    from candidate_convergence import Step, AttributedEdit, Contribution
    from edit_plan import Edit
    try:
        _require(type(step) is Step and type(step.before) is bytes and type(step.after) is bytes
            and len(step.before)<=MAX_INPUT and len(step.after)<=MAX_INPUT
            and step.before==before_raw and step.after==after_raw,
            'Repair Step is not bound to exact source bytes')
        _require(type(step.moves) is tuple and not step.moves and type(step.deferred) is tuple and not step.deferred,
            'Paragraph repair permits no moves or deferred edits')
        _require(type(step.applied) is tuple and len(step.applied)==len(repairs)<=MAX_REPAIR_EDITS,
            'Repair Step changed proposal ownership/count')
        numbers=set()
        for item,r in zip(step.applied,repairs):
            _require(type(item) is AttributedEdit and type(item.contributions) is tuple and len(item.contributions)==1,
                'Repair needs one separately attributed rule contribution')
            c=item.contributions[0]
            _require(type(c) is Contribution and type(c.pass_number) is int and 1<=c.pass_number<=MAX_REPAIR_PASSES,
                'Invalid repair contribution pass')
            numbers.add(c.pass_number)
            expected_edit=Edit(r.start,r.end,r.replacement)
            _require(_same_exact(item.edit,expected_edit) and type(item.primary_rule_id) is str
                and item.primary_rule_id==r.rule_id and _same_exact(item.rule_ids,(r.rule_id,))
                and type(c.rule_id) is str and c.rule_id==r.rule_id and _same_exact(c.edit,expected_edit),
                'Repair contribution changed rule, insertion, case edit or offset')
        _require(len(numbers)==1 and (pass_number is None or numbers=={pass_number}), 'Repair Step mixed or changed pass numbers')
    except CapturedTextUtilsParagraphError: raise
    except (AttributeError,TypeError,ValueError,KeyError,IndexError,RuntimeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Malformed bounded paragraph Step') from error

def compare_repair_step(before, after, *, before_tree, after_tree, path, step=None, **authority):
    """Independently replay one complete pass and bind original AST ownership.

    Newly inserted markers never authorize casing in the same pass. Existing
    marked paragraphs may receive their separately attributed one-byte edit.
    Returned pairs are derived node identities, not parser or write authority.
    """
    _verify_admission(before,before_tree,path,authority); _verify_admission(after,after_tree,path,authority)
    repairs=_pending(before)
    _require(bool(repairs), 'A repair Step must make strictly determined progress')
    _require(_replay_repairs(before_tree.source,repairs)==after_tree.source,
        'Repair Step differs from exact current gap/marker/case proposals')
    if step is not None: _check_step_record(step,repairs,before_tree.source,after_tree.source)
    return _bind_original_nodes(before,after,before_tree,after_tree,repairs)

def _aggregate_attribution(initial, steps):
    """Derive minimal baseline envelopes, without trusting composed patches."""
    from candidate_convergence import AttributedEdit
    from edit_plan import Edit
    if not steps: return ()
    patches=list(steps[0].applied)
    if len(steps)==2:
        first_repairs=_pending(initial)
        available={_mapped(p.first_span[0],first_repairs):p for p in initial.paragraphs
                   if p.ordinal and p.marker_nodes is None and p.first_word.islower()}
        for item in steps[1].applied:
            p=available.pop(item.edit.start,None)
            _require(p is not None, 'Second pass does not own a newly marked original starter')
            start=p.first_span[0]
            insertion=next((i for i,patch in enumerate(patches)
                            if patch.edit.start==patch.edit.end==start),None)
            causes=item.contributions
            edit=Edit(start,start+1,item.edit.replacement)
            if insertion is not None:
                old=patches.pop(insertion)
                edit=Edit(start,start+1,old.edit.replacement+item.edit.replacement)
                causes=old.contributions+causes
            causes=tuple(sorted(causes,key=lambda c:(c.pass_number,c.edit.start,c.edit.end,c.edit.replacement,c.rule_id)))
            patches.append(AttributedEdit(edit,causes[0].rule_id,tuple(sorted({c.rule_id for c in causes})),causes))
        _require(not available, 'Second pass omitted a newly marked lowercase starter')
    return tuple(sorted(patches,key=lambda p:p.edit))

def compare_repair_candidate(before, after, *, before_tree, after_tree, path,
                             candidate=None, step_trees=(), **authority):
    """Verify bounded actual pass evidence and a final canonical clean proof.

    The bridge supplies its genuine parser Trees because this module never
    executes a parser. Every intermediate Tree is independently re-certified.
    The Candidate's maps/methods are never called. Original byte replay and
    semantic node maps are independently derived from the selected baseline.
    """
    from candidate_convergence import Candidate
    _verify_admission(before,before_tree,path,authority)
    verify_proof(after,after_tree,path,**authority)
    _require(all(p.edit_start is None for p in after.paragraphs), 'Final paragraph candidate has remaining case repairs')
    expected=_pending(before,final=True)
    _require(_replay_repairs(before_tree.source,expected)==after_tree.source,
        'Final candidate changed original bytes outside canonical insertion/case ownership')
    mapping=_bind_original_nodes(before,after,before_tree,after_tree,expected)
    _require(type(step_trees) is tuple and len(step_trees)<=MAX_REPAIR_PASSES, 'Bounded immutable intermediate Trees required')
    if candidate is None:
        _require(not expected and not step_trees, 'Changed repair candidate requires separately attributed actual pass evidence')
        return mapping
    try:
        _require(type(candidate) is Candidate and type(candidate.baseline) is bytes and type(candidate.content) is bytes
            and len(candidate.baseline)<=MAX_INPUT and len(candidate.content)<=MAX_INPUT
            and candidate.baseline==before_tree.source and candidate.content==after_tree.source,
            'Candidate does not own selected original/final bytes')
        _require(type(candidate.passes) is int and type(candidate.steps) is tuple
            and candidate.passes==len(candidate.steps)==len(step_trees)<=MAX_REPAIR_PASSES,
            'Candidate repair history exceeded or changed its bound')
        prior_tree=before_tree
        for number,(step,pair) in enumerate(zip(candidate.steps,step_trees),1):
            _require(type(pair) is tuple and len(pair)==2, 'Each repair Step requires its exact two parser Trees')
            old_tree,new_tree=pair
            old=certify_repair(old_tree,path,**authority); new=certify_repair(new_tree,path,**authority)
            _require(_same_exact(old_tree,prior_tree), 'Repair Tree history is disconnected or cyclic')
            repairs=_pending(old)
            _check_step_record(step,repairs,old_tree.source,new_tree.source,pass_number=number)
            compare_repair_step(old,new,before_tree=old_tree,after_tree=new_tree,path=path,step=step,**authority)
            prior_tree=new_tree
        _require(_same_exact(prior_tree,after_tree) and _same_exact(candidate.final_tree,after_tree),
            'Repair history does not end at canonical final Tree')
        aggregate=_aggregate_attribution(before,candidate.steps)
        _require(_same_exact(candidate.edits,aggregate), 'Candidate forged or widened composed baseline edits/contributions')
    except CapturedTextUtilsParagraphError: raise
    except (AttributeError,TypeError,ValueError,KeyError,IndexError,RuntimeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Malformed bounded paragraph Candidate') from error
    return mapping
