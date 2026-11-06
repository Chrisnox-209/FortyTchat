#!/usr/bin/env python3
"""Rebuild the bundled Unicode 16/CLDR 46.1 catalogue; never used at app startup."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'unicode': 'https://www.unicode.org/Public/emoji/16.0/emoji-test.txt',
    'fr': 'https://raw.githubusercontent.com/unicode-org/cldr-json/46.1.0/cldr-json/cldr-annotations-full/annotations/fr/annotations.json',
    'derived': 'https://raw.githubusercontent.com/unicode-org/cldr-json/46.1.0/cldr-json/cldr-annotations-derived-full/annotationsDerived/fr/annotations.json',
    'license': 'https://www.unicode.org/license.txt',
}
GROUPS = {'Smileys & Emotion': 'Sourires et émotions', 'People & Body': 'Personnes et gestes',
          'Animals & Nature': 'Animaux et nature', 'Food & Drink': 'Cuisine et boissons',
          'Travel & Places': 'Voyages et lieux', 'Activities': 'Loisirs et sports',
          'Objects': 'Objets', 'Symbols': 'Symboles', 'Flags': 'Drapeaux'}


def download(item):
    key, url = item
    request = urllib.request.Request(url, headers={'User-Agent': 'FortyTchat catalogue builder'})
    with urllib.request.urlopen(request, timeout=25) as response:
        return key, response.read(8_000_000).decode('utf-8')


def build():
    with ThreadPoolExecutor(max_workers=4) as executor:
        raw = dict(executor.map(download, SOURCES.items()))
    annotations = {}
    for key in ('fr', 'derived'):
        data = json.loads(raw[key])
        body = data.get('annotations', data.get('annotationsDerived', {}))
        annotations.update(body.get('annotations', {}))
    entries, category = [], ''
    for line in raw['unicode'].splitlines():
        if line.startswith('# group: '):
            category = GROUPS.get(line[9:], '')
        if not category or '; fully-qualified' not in line:
            continue
        codes, detail = line.split(';', 1)
        emoji = ''.join(chr(int(code, 16)) for code in codes.split())
        text = detail.split('#', 1)[1].strip().split(' ', 2)[2]
        note = annotations.get(emoji) or annotations.get(emoji.replace('\ufe0f', ''), {})
        fr = note.get('tts', [text])[0]
        words = ' '.join(note.get('default', note.get('_default', [])))
        entries.append([emoji, category, fr, text, words])
    assert len(entries) > 3500 and len({e[1] for e in entries}) == 9
    target = ROOT / 'assets/emoji'
    target.mkdir(parents=True, exist_ok=True)
    payload = {'version': 'Unicode 16.0 / CLDR 46.1', 'sources': SOURCES, 'entries': entries}
    (target / 'catalog.json').write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    (target / 'UNICODE_LICENSE.txt').write_text(raw['license'], encoding='utf-8')
    print(f'{len(entries)} emojis, {len(GROUPS)} catégories, catalogue français/anglais embarqué.')


if __name__ == '__main__':
    build()
