#!/usr/bin/env python3
"""Sign a clean FortyTchat tree with the maintainer's offline Ed25519 key."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.features.releases import METADATA, PUBLIC_KEY, signing_bytes, source_digest, valid_notes
from src.features.versions import version_tuple
from tools.diagnose_session import read_version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', type=Path, required=True, help='Fichier privé en dehors du dépôt')
    parser.add_argument('--init', action='store_true', help='Créer la clé une seule fois et afficher sa partie publique')
    args = parser.parse_args()
    key_path = args.key.expanduser().absolute()
    if key_path.resolve().is_relative_to(ROOT):
        parser.error('Conserve la clé hors du dépôt.')
    try:
        if args.init:
            private = SigningKey.generate()
            key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(bytes(private))
            print(bytes(private.verify_key).hex())
            return 0
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                parser.error('La clé doit t’appartenir et être accessible à toi seul.')
            private = SigningKey(stream.read(33))
        if bytes(private.verify_key).hex() != PUBLIC_KEY:
            parser.error('Cette clé ne correspond pas à la clé publique de FortyTchat.')
        version = read_version(ROOT / 'src' / '__init__.py')
        if version_tuple(version) is None:
            parser.error('Numéro de version invalide.')
        notes = (ROOT / 'RELEASE_NOTES.md').read_text(encoding='utf-8').strip()
        if not valid_notes(notes):
            parser.error('RELEASE_NOTES.md doit contenir les nouveautés en 1 200 octets UTF-8 au plus.')
        digest = source_digest(ROOT)
        notes_digest = hashlib.sha256(notes.encode('utf-8')).digest()
        signature = private.sign(signing_bytes(version, digest, notes_digest)).signature
        token = base64.b64encode(digest + notes_digest + signature).decode('ascii')
        (ROOT / METADATA).write_text(json.dumps({'version': version, 'proof': token, 'notes': notes},
                                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'FortyTchat {version} signé.')
        return 0
    except (OSError, ValueError):
        parser.error('Impossible de lire ou créer la clé, ou de signer cet arbre.')


if __name__ == '__main__':
    sys.exit(main())
