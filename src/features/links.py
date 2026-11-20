"""Pure web-link validation and exact plain-text spans; no markup or I/O."""

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit


_WEB_LINK = re.compile(
    r'(?<![\w@.+/\\-])(?:https?://|www\.)[^\s<>"`«»“”]+',
    re.IGNORECASE,
)
_HOST_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.I)
_TRAILING_PUNCTUATION = frozenset('.,;:!?…。，、；：！？\'"`«»“”‘’')
_CLOSING_BRACKETS = {')': '(', ']': '[', '}': '{'}


def _valid_host(host: str) -> bool:
    try:
        ascii_host = host.encode('idna').decode('ascii')
    except UnicodeError:
        return False
    # A final root dot is valid DNS syntax; empty labels elsewhere are not.
    if ascii_host.endswith('.'):
        ascii_host = ascii_host[:-1]
    if not ascii_host or len(ascii_host) > 253:
        return False
    if re.fullmatch(r'[0-9.]+', ascii_host):
        try:
            ipaddress.IPv4Address(ascii_host)
        except ipaddress.AddressValueError:
            return False
        return True
    return all(_HOST_LABEL.fullmatch(label) for label in ascii_host.split('.'))


def normalize_web_url(value: str) -> str | None:
    """Validate HTTP(S), adding only ``https://`` to a bare ``www.`` URL.

    Local DNS names and IP addresses are accepted without a network lookup.
    Literal controls, invisible formatting characters, whitespace and browser
    authority ambiguities are rejected before ``urlsplit`` can discard them.
    """
    if not isinstance(value, str) or not value:
        return None
    if any(char.isspace() or unicodedata.category(char) in {'Cc', 'Cf', 'Cs'}
           for char in value):
        return None
    if any(char in value for char in '<>"`\\'):
        return None

    bare_www = value[:4].lower() == 'www.'
    url = 'https://' + value if bare_www else value
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc:
            return None
        host = parsed.hostname
        port = parsed.port  # Also rejects non-numeric and out-of-range ports.
    except (ValueError, UnicodeError):
        return None
    if not host:
        return None

    # A bare www.name@example.org is an email address, not a web URL.
    if parsed.netloc.count('@') > 1 or (bare_www and '@' in parsed.netloc):
        return None
    authority = parsed.netloc.rsplit('@', 1)[-1]
    if '@' in parsed.netloc and not parsed.netloc.rsplit('@', 1)[0]:
        return None
    if authority.startswith('['):
        closing = authority.find(']')
        suffix = authority[closing + 1:]
        if closing < 0 or (suffix and not suffix.startswith(':')):
            return None
        try:
            ipaddress.IPv6Address(host)
        except ipaddress.AddressValueError:
            return None
    else:
        if '[' in authority or ']' in authority or not _valid_host(host):
            return None
        # hostname is lowercased by urlsplit; Unicode lowercasing can change its
        # length, so slice the original authority at its literal port separator.
        separator = authority.find(':')
        suffix = authority[separator:] if separator >= 0 else ''
    if suffix:
        digits = suffix[1:]
        if not suffix.startswith(':') or not digits or not digits.isascii() or not digits.isdecimal():
            return None
        if port is None:
            return None
    if bare_www and host.rstrip('.').lower() == 'www':
        return None
    return url


def _inside_other_scheme(text: str, start: int) -> bool:
    """Avoid treating the tail of file://www... or mailto:www... as a link."""
    index = start
    while index and text[index - 1] == '/':
        index -= 1
    if not index or text[index - 1] != ':':
        return False
    end = index - 1
    index = end
    while index and text[index - 1].isascii() and (
        text[index - 1].isalnum() or text[index - 1] in '+.-'
    ):
        index -= 1
    return index < end and text[index].isalpha()


def _link_end(text: str, start: int, end: int) -> int:
    candidate = text[start:end]
    counts = {char: candidate.count(char)
              for char in '()[]{}'}
    while end > start:
        char = text[end - 1]
        if char in _TRAILING_PUNCTUATION:
            end -= 1
        elif char in _CLOSING_BRACKETS and counts[char] > counts[_CLOSING_BRACKETS[char]]:
            counts[char] -= 1
            end -= 1
        else:
            break
    return end


def web_links(text: str) -> list[tuple[int, int, str]]:
    """Return non-overlapping ``(start, end, url)`` spans in the exact text.

    End positions are exclusive Python string indices. Common sentence-ending
    punctuation and unmatched closing brackets are excluded; balanced brackets
    and percent encodings remain part of a URL. Code-block policy is the caller's.
    """
    links = []
    for match in _WEB_LINK.finditer(text):
        start = match.start()
        if _inside_other_scheme(text, start):
            continue
        end = _link_end(text, start, match.end())
        url = normalize_web_url(text[start:end])
        if url is not None:
            links.append((start, end, url))
    return links
