"""Publisher-signed release metadata, verifiable without the network."""
from dataclasses import dataclass, replace
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .versions import version_tuple

PUBLIC_KEY = '3114aad77dcb82b77e4dd21bf9b65c1fd620105f4ceb8e4ec14c057c2ae09ff4'
LEGACY_MAX = (2, 8, 0)
MARKER = 'release_v1'
METADATA = Path('src/official-release.json')
SOURCE_TREES = ('src', 'assets', 'tools')
SOURCE_FILES = ('main.py', 'requirements.txt', 'install.sh', 'uninstall.sh',
                'diagnostic-session.sh', 'network.example.json')
DOMAIN = b'FortyTchat/official-release/v1\0'
TOKEN_LENGTH = 172
FEATURE_PARTS = 6
MAX_NOTES_BYTES = 1200


@dataclass(frozen=True)
class Release:
    version: str
    digest: bytes
    notes_digest: bytes
    token: str
    notes: str = ''

    def features(self):
        return [MARKER, *(self.token[i:i + 32] for i in range(0, TOKEN_LENGTH, 32))]

    def matches_notes(self, notes):
        return valid_notes(notes) and hashlib.sha256(notes.encode('utf-8')).digest() == self.notes_digest


def valid_notes(notes):
    if not isinstance(notes, str) or not 1 <= len(notes) <= MAX_NOTES_BYTES or not notes.strip():
        return False
    if any((ord(char) < 32 and char != '\n') or 127 <= ord(char) < 160 for char in notes):
        return False
    try:
        return len(notes.encode('utf-8')) <= MAX_NOTES_BYTES
    except UnicodeEncodeError:
        return False


def signing_bytes(version, digest, notes_digest):
    return DOMAIN + version.encode('ascii') + b'\0' + digest + notes_digest


def verify_release(version, token):
    if not version_tuple(version) or not isinstance(token, str) or len(token) != TOKEN_LENGTH:
        return None
    try:
        proof = base64.b64decode(token, validate=True)
        if len(proof) != 128 or base64.b64encode(proof).decode('ascii') != token:
            return None
        digest, notes_digest, signature = proof[:32], proof[32:64], proof[64:]
        VerifyKey(bytes.fromhex(PUBLIC_KEY)).verify(signing_bytes(version, digest, notes_digest), signature)
    except (ValueError, BadSignatureError, binascii.Error):
        return None
    return Release(version, digest, notes_digest, token)


def advertised_release(version, features):
    """Interpret proof tokens within the existing client's bounded feature list."""
    start = len(features) - FEATURE_PARTS - 1
    if start < 0 or features[start] != MARKER or features.count(MARKER) != 1:
        return None
    return verify_release(version, ''.join(features[start + 1:]))


def load_release(root):
    try:
        path = root / METADATA
        if path.is_symlink() or not path.is_file():
            return None
        with path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {'version', 'proof', 'notes'}:
            return None
        proof = verify_release(value['version'], value['proof'])
        if proof and proof.matches_notes(value['notes']):
            return replace(proof, notes=value['notes'])
        return None
    except (OSError, ValueError):
        return None


def source_digest(root):
    paths = [Path(name) for name in SOURCE_FILES]
    for directory in SOURCE_TREES:
        base = root / directory
        if not base.is_dir() or base.is_symlink():
            raise OSError('Dossier de publication manquant ou symbolique')
        for parent, directories, files in os.walk(base):
            parent = Path(parent)
            directories[:] = [name for name in directories if name != '__pycache__']
            if any((parent / name).is_symlink() for name in directories):
                raise OSError('Dossier symbolique dans la publication')
            paths.extend((parent / name).relative_to(root) for name in files)
    result = hashlib.sha256()
    for relative in sorted(paths):
        if relative == METADATA:
            continue
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise OSError('Fichier de publication manquant ou symbolique')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        result.update(relative.as_posix().encode('utf-8') + b'\0' + digest.digest())
    return result.digest()


def verified_tree(root, version=None):
    release = load_release(root)
    if release is None or (version is not None and version != release.version):
        return None
    try:
        return release if source_digest(root) == release.digest else None
    except OSError:
        return None
