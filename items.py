global node, repo

from bundlewrap.exceptions import BundleError

roles = node.metadata.get('forgejo/roles')

users, directories, downloads, files, svc_systemd = {}, {}, {}, {}, {}

actions = {
    'forgejo_daemon_reload': {
        'command': 'systemctl daemon-reload',
        'triggered': True,
    },
}


if 'server' in roles:
    cfg = node.metadata.get('forgejo/server')
    home = cfg.get('home')

    for key in ('domain', 'reverse_proxy'):
        if not cfg.get(key):
            raise BundleError(f"{node.name}: forgejo/server/{key} must be set in the node "
                              f"(reverse_proxy may also come from reverse_proxy_node)")

    users['git'] = {
        'home': home,
        'shell': '/bin/bash',  # git over the system sshd
    }

    # Forgejo creates everything else below its home itself.
    directories[home] = {'owner': 'git', 'group': 'git', 'mode': '0750', 'needs': ['user:git']}
    directories[f'{home}/custom'] = {'owner': 'git', 'group': 'git', 'mode': '0750', 'needs': ['user:git']}
    directories['/etc/forgejo'] = {'owner': 'root', 'group': 'git', 'mode': '0750'}

    downloads['/usr/local/bin/forgejo'] = {
        'url': f'https://codeberg.org/forgejo/forgejo/releases/download/v{cfg.get("version")}/forgejo-{cfg.get("version")}-linux-amd64',
        'sha256': cfg.get('sha256'),
        'mode': '0755',
        'triggers': ['svc_systemd:forgejo:restart'],
    }

    files['/etc/forgejo/app.ini'] = {
        'source': 'etc/forgejo/app.ini.j2',
        'content_type': 'jinja2',
        'context': {
            'cfg': cfg,
            'secret_key': repo.vault.password_for('forgejo_secret_key', length=64).value,
        },
        'owner': 'root',
        'group': 'git',
        'mode': '0640',
        'triggers': ['svc_systemd:forgejo:restart'],
    }

    files['/etc/systemd/system/forgejo.service'] = {
        'source': 'etc/systemd/system/forgejo.service.j2',
        'content_type': 'jinja2',
        'context': {'home': home},
        'triggers': ['action:forgejo_daemon_reload', 'svc_systemd:forgejo:restart'],
    }

    svc_systemd['forgejo'] = {
        'needs': [
            'user:git',
            f'directory:{home}/custom',
            'download:/usr/local/bin/forgejo',
            'file:/etc/forgejo/app.ini',
            'file:/etc/systemd/system/forgejo.service',
            'pkg_apt:git',
        ],
    }


if 'runner' in roles:
    cfg = node.metadata.get('forgejo/runner')
    home = cfg.get('home')

    users['runner'] = {
        'home': home,
        'shell': '/usr/sbin/nologin',
        'groups': ['docker'],  # root-equivalent: this VM must do nothing else
        'needs': ['pkg_apt:docker-ce'],  # the docker group has to exist first
    }
    directories[home] = {'owner': 'runner', 'group': 'runner', 'mode': '0750', 'needs': ['user:runner']}

    directories['/etc/forgejo-runner'] = {'owner': 'root', 'group': 'runner', 'mode': '0750'}

    downloads['/usr/local/bin/forgejo-runner'] = {
        'url': f'https://code.forgejo.org/forgejo/runner/releases/download/v{cfg.get("version")}/forgejo-runner-{cfg.get("version")}-linux-amd64',
        'sha256': cfg.get('sha256'),
        'mode': '0755',
        'triggers': ['svc_systemd:forgejo-runner:restart'],
    }

    files['/etc/forgejo-runner/config.yaml'] = {
        'source': 'etc/forgejo-runner/config.yaml.j2',
        'content_type': 'jinja2',
        'context': {'cfg': cfg},
        'owner': 'root',
        'group': 'runner',
        'mode': '0640',
        'triggers': ['svc_systemd:forgejo-runner:restart'],
    }

    files['/etc/systemd/system/forgejo-runner.service'] = {
        'source': 'etc/systemd/system/forgejo-runner.service.j2',
        'content_type': 'jinja2',
        'context': {'home': home},
        'triggers': ['action:forgejo_daemon_reload', 'svc_systemd:forgejo-runner:restart'],
    }

    svc_systemd['forgejo-runner'] = {
        'needs': [
            'user:runner',
            'download:/usr/local/bin/forgejo-runner',
            'file:/etc/forgejo-runner/config.yaml',
            'file:/etc/systemd/system/forgejo-runner.service',
        ],
    }
