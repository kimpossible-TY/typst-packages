"""Pure, versioned source repertoires, independent of live resource availability.

These names describe reviewed experiments, not general Unicode/font support.
This module never reads files, resolves fonts, probes a compiler or normalizes
source. Resource declarations and actual captures belong to the compiler.
"""
from pathlib import Path
from types import MappingProxyType

ASCII_RESOURCE_PROFILE = 'embedded-ascii-v1'
APPLEGOTHIC_RESOURCE_PROFILE = 'embedded-applegothic-ko-v1'
KNOWN_RESOURCE_PROFILES = frozenset((ASCII_RESOURCE_PROFILE, APPLEGOTHIC_RESOURCE_PROFILE))
REPERTOIRE_VERSIONS = MappingProxyType({
    ASCII_RESOURCE_PROFILE: 'ascii-v1',
    APPLEGOTHIC_RESOURCE_PROFILE: 'applegothic-ko-v1',
})
_PROTECTED_PATHS = MappingProxyType({
    ASCII_RESOURCE_PROFILE: (),
    APPLEGOTHIC_RESOURCE_PROFILE: (Path('/System/Library/Fonts/Supplemental/AppleGothic.ttf'),),
})
_KOREAN_PUNCTUATION = frozenset('·–—‘’“”…₩、。〈〉《》「」『』【】')


def validate_resource_profile(profile):
    """Return an exact known historical identifier, without consulting the OS."""
    if type(profile) is not str or profile not in KNOWN_RESOURCE_PROFILES:
        raise ValueError('Unknown explicit compile resource profile')
    return profile


def resource_profile_protected_paths(profile):
    """Fixed protected source-collision paths, without checking availability."""
    return _PROTECTED_PATHS[validate_resource_profile(profile)]


def validate_source_repertoire(profile, source):
    """Validate exact bytes only; never widen the closed-core AST grammar.

    One optional leading UTF-8 BOM is accepted. Korean v1 adds only modern
    precomposed syllables, modern compatibility jamo, and 21 reviewed punctuation
    characters. Conjoining/NFD jamo, fillers and all other non-ASCII refuse.
    """
    validate_resource_profile(profile)
    if type(source) is not bytes:
        raise ValueError('Compile source must be bytes')
    payload = source[3:] if source.startswith(b'\xef\xbb\xbf') else source
    if payload.isascii():
        return
    if profile == ASCII_RESOURCE_PROFILE:
        raise ValueError('embedded-ascii-v1 requires ASCII source; Unicode fidelity is unsupported')
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('Resource profile requires valid UTF-8 source') from error
    for character in text:
        codepoint = ord(character)
        if not (codepoint < 128 or 0xAC00 <= codepoint <= 0xD7A3
                or 0x3131 <= codepoint <= 0x3163 or character in _KOREAN_PUNCTUATION):
            raise ValueError(f'{profile} refuses source codepoint U+{codepoint:04X}')
