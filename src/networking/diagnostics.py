"""Local bind tests prove local availability only, never remote reachability."""
import errno
import socket

from .interfaces import interfaces


def socket_error(exc, protocol, port):
    if getattr(exc, 'errno', None) == errno.EADDRINUSE:
        return f'{protocol} {port} déjà occupé : vérifiez les instances et ss -tuln.'
    if getattr(exc, 'errno', None) in (errno.EACCES, errno.EPERM):
        return f'{protocol} {port} refusé par la politique locale ; aucune règle système modifiée.'
    return f'{protocol} {port} indisponible (erreur {getattr(exc, "errno", "inconnue")}).'


def probe_ports(udp_port=39741, tcp_port=39742):
    rows = []
    for label, kind, port in [('UDP', socket.SOCK_DGRAM, udp_port), ('TCP', socket.SOCK_STREAM, tcp_port)]:
        try:
            with socket.socket(socket.AF_INET, kind) as sock:
                sock.bind(('0.0.0.0', port))
                if kind == socket.SOCK_STREAM:
                    sock.listen(1)
            rows.append(f'{label} {port} : ouverture locale possible. Accessibilité depuis un autre PC non vérifiée.')
        except OSError as exc:
            rows.append(socket_error(exc, label, port))
    return {'interfaces': [i.public() for i in interfaces()], 'tests': rows,
            'conclusion': 'Un test sur deux PC reste nécessaire ; aucun scan réseau effectué.'}
