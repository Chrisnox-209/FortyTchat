from dataclasses import dataclass, field
import time

HEARTBEAT = 25
PEER_TIMEOUT = 90


@dataclass
class Peer:
    sid: str
    nick: str
    status: str = 'online'
    host: str = ''
    port: int = 0
    direct_seen: float = 0.0
    timeout: float = PEER_TIMEOUT
    relay: bool = False
    caps: tuple = ()
    avatar: int = -1
    avatar_hash: str = ''
    avatar_image: str = ''
    contact_id: str = ''
    bucket: object = field(default=None, repr=False)
    wizz: object = field(default=None, repr=False)
    social_bucket: object = field(default=None, repr=False)
    typing_bucket: object = field(default=None, repr=False)

    @property
    def direct(self):
        return bool(self.host and self.port and self.direct_seen > 0 and time.monotonic() - self.direct_seen < self.timeout)

    def public(self):
        return {'sid': self.sid, 'nick': self.nick, 'status': self.status, 'direct': self.direct,
                'relay': self.relay, 'caps': self.caps, 'avatar': self.avatar,
                'avatar_hash': self.avatar_hash, 'avatar_image': self.avatar_image, 'contact_id': self.contact_id}
