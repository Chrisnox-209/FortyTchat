"""Locally generated update instructions; never accept commands from a peer."""
import shlex


DEFAULT_REPOSITORY = '~/.FortyTchat'


def update_command(repository=DEFAULT_REPOSITORY):
    """Build a POSIX shell block with a literal, safely quoted repository path."""
    if not isinstance(repository, str) or not repository or any(ord(c) < 32 or ord(c) == 127 for c in repository):
        raise ValueError('Choisis un chemin absolu ou commençant par ~/.')
    if repository == DEFAULT_REPOSITORY:
        destination = '"$HOME/.FortyTchat"'
    elif repository.startswith('~/'):
        destination = '"$HOME"/' + shlex.quote(repository[2:])
    elif repository.startswith('/'):
        destination = shlex.quote(repository)
    else:
        raise ValueError('Choisis un chemin absolu ou commençant par ~/.')
    missing = ('Dépôt introuvable : vérifie ~/.FortyTchat.' if repository == DEFAULT_REPOSITORY
               else 'Dépôt introuvable : vérifie le dossier choisi pour la mise à jour.')
    return (
        '(\n'
        '  echec() { printf \'%s\\n\' "$1" >&2; exit 1; }\n\n'
        f'  cd {destination} || echec {shlex.quote(missing)}\n\n'
        '  python3 -c \'from tools.uninstall_user import data_home, stop_installed; stop_installed(data_home() / "fortytchat" / "app")\' \\\n'
        '    || echec \'Fermeture impossible : quitte FortyTchat depuis son icône puis réessaie.\'\n\n'
        '  git pull --ff-only \\\n'
        '    || echec \'Git a échoué : installation non lancée. Consulte le message ci-dessus.\'\n\n'
        '  sh ./install.sh \\\n'
        '    || echec \'Installation échouée : consulte le message ci-dessus avant de réessayer.\'\n\n'
        '  printf \'%s\\n\' \'Mise à jour terminée.\'\n'
        ')'
    )
