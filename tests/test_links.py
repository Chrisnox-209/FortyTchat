import pytest

from src.features.links import normalize_web_url, web_links


@pytest.mark.parametrize('value, expected', [
    ('https://example.org', 'https://example.org'),
    ('HTTP://Example.ORG:80/A?x=1&y=2#Part', 'HTTP://Example.ORG:80/A?x=1&y=2#Part'),
    ('www.example.org/a?x=1&y=2#part', 'https://www.example.org/a?x=1&y=2#part'),
    ('WWW.Example.ORG', 'https://WWW.Example.ORG'),
    ('http://localhost:8000', 'http://localhost:8000'),
    ('http://poste-cluster:8080', 'http://poste-cluster:8080'),
    ('http://127.0.0.1:65535/a', 'http://127.0.0.1:65535/a'),
    ('http://[::1]:8000/a', 'http://[::1]:8000/a'),
    ('https://[2001:db8::1]/', 'https://[2001:db8::1]/'),
    ('https://example.org.:443/', 'https://example.org.:443/'),
    ('https://équipe.fr/été?q=été#résumé', 'https://équipe.fr/été?q=été#résumé'),
    ('https://例え.テスト/会議', 'https://例え.テスト/会議'),
    ('https://İstanbul.example:443/été', 'https://İstanbul.example:443/été'),
    ('https://example.org/a%20b%29?q=%0A', 'https://example.org/a%20b%29?q=%0A'),
    ('https://user:password@example.org/path', 'https://user:password@example.org/path'),
    ("https://example.org/that's_(fine)", "https://example.org/that's_(fine)"),
])
def test_normalize_accepts_web_urls_and_preserves_their_content(value, expected):
    assert normalize_web_url(value) == expected


@pytest.mark.parametrize('value', [
    '', None, 42, 'example.org', '//example.org',
    'javascript:alert(1)', 'javascript:https://example.org',
    'data:text/html,hello', 'vbscript:hello', 'file:///tmp/example',
    'mailto:www.example.org', 'ftp://example.org',
    'https:///path', 'https://', 'http:example.org',
    'https://example.org:', 'https://example.org:abc',
    'https://example.org:+80', 'https://example.org:８０',
    'https://example.org:65536', 'https://example.org:-1',
    'https://-example.org', 'https://example-.org',
    'https://example..org', 'https://example_org',
    'https://999.0.0.1', 'https://127.1',
    'https://[::1]extra', 'https://[invalid]/', 'https://::1/',
    'https://@example.org', 'https://user@other@example.org',
    'www.', 'www.:80', 'www.example.org@example.net',
    'https://example.org/a b', ' https://example.org',
    '\nhttps://example.org', 'https://exam\tple.org',
    'https://example.org/\x00tail', 'https://example.org/\x7f',
    'https://example.org/\u0080', 'https://example.org/\u202eabc',
    'https://example.org/\ud800', 'https://example.org/\u00a0tail',
    'https://example.org\\@other.org', 'https://example.org/<tag>',
    'https://example.org/"quote', 'https://example.org/`code',
    'https://' + 'a' * 64 + '.org',
])
def test_normalize_rejects_other_schemes_and_malformed_urls(value):
    assert normalize_web_url(value) is None


def test_spans_use_exact_python_string_indices_with_unicode_and_multiple_links():
    text = '😀 Équipe : https://équipe.fr/été?a=1&b=2#résumé puis WWW.Example.org/a%20b.'
    first = 'https://équipe.fr/été?a=1&b=2#résumé'
    second = 'WWW.Example.org/a%20b'
    first_start = text.index(first)
    second_start = text.index(second)
    assert web_links(text) == [
        (first_start, first_start + len(first), first),
        (second_start, second_start + len(second), 'https://' + second),
    ]
    assert text == '😀 Équipe : https://équipe.fr/été?a=1&b=2#résumé puis WWW.Example.org/a%20b.'


@pytest.mark.parametrize('text, visible', [
    ('Voir (https://example.org/a).', 'https://example.org/a'),
    ('Voir [https://example.org/a],', 'https://example.org/a'),
    ('Voir {https://example.org/a}!', 'https://example.org/a'),
    ('"https://example.org/a"', 'https://example.org/a'),
    ("'https://example.org/a'", 'https://example.org/a'),
    ('«https://example.org/a»', 'https://example.org/a'),
    ('“https://example.org/a”', 'https://example.org/a'),
    ('‘https://example.org/a’', 'https://example.org/a'),
    ('https://example.org/a...?!', 'https://example.org/a'),
    ('(https://example.org/a_(b)).', 'https://example.org/a_(b)'),
    ('(https://example.org/a_(b(c))).', 'https://example.org/a_(b(c))'),
    ('[https://example.org/a[b]].', 'https://example.org/a[b]'),
    ('(http://[::1]:8000/a).', 'http://[::1]:8000/a'),
    ('https://example.org/a%28b%29.', 'https://example.org/a%28b%29'),
    ("https://example.org/that's_fine.", "https://example.org/that's_fine"),
    ('https://example.org/?x=1&y=2&', 'https://example.org/?x=1&y=2&'),
])
def test_detection_trims_prose_punctuation_and_preserves_balanced_url_content(text, visible):
    start = text.index(visible)
    assert web_links(text) == [(start, start + len(visible), visible)]


@pytest.mark.parametrize('text', [
    'alice@www.example.org', 'alice.www.example.org',
    'alice+www.example.org@example.net', 'www.example.org@example.net',
    'file://www.example.org', 'file:///www.example.org',
    'file:///home/www.example.org', r'C:\www.example.org',
    'mailto:www.example.org', 'javascript:https://example.org',
    'ftp://www.example.org', 'prefixhttps://example.org',
    'https://example.org:bad/path', 'https://example.org:99999',
    'https://bad..host/path', 'https://example.org/path\x00tail',
    'https://example.org/\u202eabc',
])
def test_detection_ignores_emails_other_schemes_and_invalid_urls(text):
    assert web_links(text) == []


def test_separate_lines_and_local_addresses_are_detected():
    text = 'http://localhost:8000\nhttp://poste-cluster:8080\thttp://192.168.1.2/'
    expected = ['http://localhost:8000', 'http://poste-cluster:8080', 'http://192.168.1.2/']
    assert [url for _, _, url in web_links(text)] == expected
    assert [text[start:end] for start, end, _ in web_links(text)] == expected


def test_detection_leaves_code_block_policy_to_the_caller():
    text = '====text\nhttps://example.org/code\n===='
    start = text.index('https://')
    url = 'https://example.org/code'
    assert web_links(text) == [(start, start + len(url), url)]
