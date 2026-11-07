"""Pure, exact source-profile to closed-grammar version selection.

Historical names are immutable language choices, not aliases for the broadest
available grammar. No parsing, filesystem access or resource lookup occurs here.
"""
from types import MappingProxyType

CLOSED_CORE_SOURCE_PROFILE = 'closed-core-v1'
STRUCTURED_CORE_SOURCE_PROFILE = 'structured-core-v1'
CLOSED_CORE_GRAMMAR_VERSION = 'closed-core-markup-v1'
STRUCTURED_CORE_GRAMMAR_VERSION = 'closed-core-markup-v2'
GRAMMAR_VERSIONS = MappingProxyType({
    CLOSED_CORE_SOURCE_PROFILE: CLOSED_CORE_GRAMMAR_VERSION,
    STRUCTURED_CORE_SOURCE_PROFILE: STRUCTURED_CORE_GRAMMAR_VERSION,
})
KNOWN_SOURCE_PROFILES = frozenset(GRAMMAR_VERSIONS)


def validate_source_profile(profile):
    """Return only an exact known source profile; never normalize or alias."""
    if type(profile) is not str or profile not in KNOWN_SOURCE_PROFILES:
        raise ValueError('Unknown explicit source profile')
    return profile


def grammar_for_source_profile(profile):
    """Return the fixed grammar version for the caller-selected profile."""
    return GRAMMAR_VERSIONS[validate_source_profile(profile)]
