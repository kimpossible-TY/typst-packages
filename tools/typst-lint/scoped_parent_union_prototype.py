"""Bounded independent union of parent-scoped source-only proposals.

Each baseline is independently reissued, then the complete union is reparsed
and the parent binding, scope identities and existing findings are checked.
Completed baseline plans are retained when a site or union proof is withheld.
A report byte-limit failure rejects the entire response. No filesystem writes,
compilation or rendering authority are conveyed.
"""
import json

import conditional_modern_highlight as modern
import scoped_parent_transfer_prototype as parent
from typst_lint import InputError, SafetyError, require

MAX_SITES = 4
MAX_WORK = 5 * parent.MAX_PREVIEW_WORK
MAX_BYTES = 4 * parent.MAX_BYTES
CONDITIONS = parent.PREVIEW_CONDITIONS + (
    'Only the listed source edits were revalidated together; no other plan is composed.',
    'The union remains conditional on runtime selection, execution and rendering.',
    'Withheld proofs retain completed baseline plans; exceeding the report byte limit rejects the entire response.',
)


def preview_union(evidence, path, starts, *, max_work=MAX_WORK, max_bytes=MAX_BYTES):
    """Return verified single-site plans and, if possible, their verified union."""
    require(type(evidence) is modern.ModernHighlightAnalyzer and type(path) is str
            and type(starts) in (tuple, list) and 1 < len(starts) <= MAX_SITES
            and all(type(v) is int and v >= 0 for v in starts)
            and len(set(starts)) == len(starts) and type(max_work) is int and 0 < max_work <= MAX_WORK
            and type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES,
            'Invalid parent union request')
    modern.ModernHighlightAnalyzer._check(evidence)
    result = dict(path=path, status='INCOMPLETE', baseline=[], union=None,
                  conditions=CONDITIONS, source_union_revalidated=False,
                  composition_proved=False, runtime_binding_proved=False,
                  execution_proved=False, coverage_complete=False,
                  render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
    remaining = max_work
    try:
        for start in starts:
            if remaining <= 0:
                raise SafetyError('Parent union work budget exceeded')
            row = parent.preview_one(evidence, path, start,
                                     max_work=min(remaining, parent.MAX_PREVIEW_WORK))
            remaining -= row['work_used']
            result['baseline'].append(row)
        if remaining <= 0:
            raise SafetyError('Parent union work budget exceeded')
        union = parent._preview_selected(evidence, path, tuple(starts),
                                         max_work=min(remaining, parent.MAX_PREVIEW_WORK))
        remaining -= union['work_used']
        expected = sorted((e['start'], e['end'], e['replacement'], e['rule_id'])
                          for row in result['baseline'] for e in row['edits'])
        actual = sorted((e['start'], e['end'], e['replacement'], e['rule_id']) for e in union['edits'])
        if expected != actual or len(actual) != len(starts):
            raise SafetyError('Union edits differ from independently verified plans')
        if len({row['baseline_sha256'] for row in result['baseline'] + [union]}) != 1:
            raise SafetyError('Union and baseline captured sources differ')
        union['verification'] = 'conditional-parent-union-source-only'
        union['conditions'] = CONDITIONS
        union['source_union_revalidated'] = True
        result['union'] = union
        result['source_union_revalidated'] = True
        result['status'] = 'CONDITIONAL'
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        modern.ModernHighlightAnalyzer._check(evidence)
        result['reason'] = str(error)
    modern.ModernHighlightAnalyzer._check(evidence)
    # A refused inner proof does not expose its consumed work; report only
    # the completed calls in that case instead of claiming an exact total.
    result['completed_work'] = max_work - remaining
    result['work_used'] = result['completed_work'] if result['status'] == 'CONDITIONAL' else None
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    require(len(encoded.encode('utf-8')) + 2 * encoded.count('\n') <= max_bytes,
            'Parent union report byte budget exceeded')
    return result
