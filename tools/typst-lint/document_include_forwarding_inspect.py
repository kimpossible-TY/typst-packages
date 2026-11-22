"""Optional include forwarding inspection using explicit captured resources.

This performs no compilation and does not change the original unresolved
inventory. The selected package root and all source bytes are captured again;
existing compile-path validation independently replays the reported sites.
"""
import document_resources
import include_context
import include_forwarding_evidence as forwarding
from document_original_compile_cli import _include_forwarding
from typst_lint import InputError, SafetyError, require


def inspect(context, package_path, cache_path, *, max_items):
    require(type(max_items) is int and 0 < max_items <= 100_000,
            'Invalid include forwarding report item budget')
    include_context.assert_include_context(context)
    roots = dict(project=context.project_root, packages=package_path, cache=cache_path)
    requests = [document_resources.ResourceRequest('project', item.path, 'source')
                for item in context.sources]
    requests += [document_resources.ResourceRequest('packages', 'local/math-book/0.2.0/' + name, 'package')
                 for name in ('typst.toml', 'lib.typ')]
    try:
        snapshot = document_resources.capture(roots, requests)
    except (InputError, SafetyError, OSError) as error:
        include_context.assert_include_context(context)
        return forwarding._unavailable(context, 'Inspection resource capture unavailable: ' + str(error)), 1
    try:
        result, used = _include_forwarding(context, snapshot, forwarding.MAX_EVIDENCE_ITEMS)
    except InputError as error:
        if str(error) not in ('Include forwarding work budget exceeded',
                              'Include forwarding report byte budget exceeded',
                              'Include forwarding evidence limit exceeded'):
            raise
        include_context.assert_include_context(context)
        document_resources.assert_capture(snapshot)
        return forwarding._unavailable(context, str(error)), 1
    if used > max_items:
        include_context.assert_include_context(context)
        document_resources.assert_capture(snapshot)
        return forwarding._unavailable(context, 'Remaining document report item budget is insufficient'), 1
    # No compiler dependency closure or execution proof is implied.
    result['inspection_resource_snapshot'] = True
    result['compiler_observation'] = False
    return result, used
