global node, repo

import json
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

    # Forgejo does not create the files behind INTERNAL_TOKEN_URI / JWT_SECRET_URI
    # itself; generate them once with its own generator.
    actions['forgejo_generate_secrets'] = {
        'command': (
            'umask 077 && '
            f'sudo -u git /usr/local/bin/forgejo generate secret INTERNAL_TOKEN > {home}/custom/internal_token && '
            f'sudo -u git /usr/local/bin/forgejo generate secret JWT_SECRET > {home}/custom/oauth2_jwt_secret && '
            f'chown git:git {home}/custom/internal_token {home}/custom/oauth2_jwt_secret'
        ),
        'unless': f'test -s {home}/custom/internal_token && test -s {home}/custom/oauth2_jwt_secret',
        'needs': [
            'user:git',
            f'directory:{home}/custom',
            'download:/usr/local/bin/forgejo',
        ],
    }

    files['/etc/systemd/system/forgejo.service'] = {
        'source': 'etc/systemd/system/forgejo.service.j2',
        'content_type': 'jinja2',
        'context': {'home': home},
        'triggers': ['action:forgejo_daemon_reload', 'svc_systemd:forgejo:restart'],
    }

    if cfg.get('db_snapshot_time'):
        files['/etc/systemd/system/forgejo-db-snapshot.service'] = {
            'source': 'etc/systemd/system/forgejo-db-snapshot.service.j2',
            'content_type': 'jinja2',
            'context': {'home': home},
            'triggers': ['action:forgejo_daemon_reload'],
        }
        files['/etc/systemd/system/forgejo-db-snapshot.timer'] = {
            'source': 'etc/systemd/system/forgejo-db-snapshot.timer.j2',
            'content_type': 'jinja2',
            'context': {'time': cfg.get('db_snapshot_time')},
            'triggers': ['action:forgejo_daemon_reload'],
        }
        svc_systemd['forgejo-db-snapshot.timer'] = {
            'needs': [
                'file:/etc/systemd/system/forgejo-db-snapshot.service',
                'file:/etc/systemd/system/forgejo-db-snapshot.timer',
                'pkg_apt:sqlite3',
                'user:git',
            ],
        }

    svc_systemd['forgejo'] = {
        'needs': [
            'user:git',
            f'directory:{home}/custom',
            'download:/usr/local/bin/forgejo',
            'action:forgejo_generate_secrets',
            'file:/etc/forgejo/app.ini',
            'file:/etc/systemd/system/forgejo.service',
            'pkg_apt:git',
        ],
    }


if 'runner' in roles:
    cfg = node.metadata.get('forgejo/runner')
    home = cfg.get('home')

    if not cfg.get('connections'):
        raise BundleError(f"{node.name}: forgejo/runner/connections must be set in the node "
                          f"(uuid + token from Forgejo's 'Create new runner')")

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


if 'pull' in roles:
    cfg = node.metadata.get('forgejo/pull')
    puser = cfg.get('user')

    for key in ('url', 'owner', 'token'):
        if not cfg.get(key):
            raise BundleError(f"{node.name}: forgejo/pull/{key} must be set in the node")

    # `user: root` is for hosts where targets need docker (type git + compose);
    # then no dedicated user is created.
    user_needs = [] if puser == 'root' else [f'user:{puser}']
    if puser != 'root':
        users[puser] = {
            'home': '/var/lib/forgejo-pull',
            'shell': '/usr/sbin/nologin',
        }
    directories['/var/lib/forgejo-pull'] = {'owner': puser, 'group': puser, 'mode': '0750', 'needs': user_needs}
    directories['/etc/forgejo-pull'] = {'owner': 'root', 'group': puser, 'mode': '0750', 'needs': user_needs}

    files['/usr/local/bin/forgejo-pull'] = {
        'source': 'usr/local/bin/forgejo-pull',
        'mode': '0755',
    }
    files['/etc/forgejo-pull/forgejo.env'] = {
        'content': (
            f'FORGEJO_URL={cfg.get("url")}\n'
            f'FORGEJO_OWNER={cfg.get("owner")}\n'
            f'FORGEJO_TOKEN={cfg.get("token")}\n'
            f'STATE_DIR=/var/lib/forgejo-pull\n'
        ),
        'owner': 'root',
        'group': puser,
        'mode': '0640',
        'needs': ['directory:/etc/forgejo-pull'],
    }
    files['/etc/systemd/system/forgejo-pull@.service'] = {
        'source': 'etc/systemd/system/forgejo-pull@.service.j2',
        'content_type': 'jinja2',
        'context': {'user': puser},
        'triggers': ['action:forgejo_daemon_reload'],
    }
    files['/etc/systemd/system/forgejo-pull@.timer'] = {
        'source': 'etc/systemd/system/forgejo-pull@.timer.j2',
        'content_type': 'jinja2',
        'context': {'interval': cfg.get('interval')},
        'triggers': ['action:forgejo_daemon_reload'],
    }

    hook = cfg.get('webhook')
    if hook.get('enabled'):
        # Debian's `webhook` package: one daemon, /etc/webhook.conf, runs as root.
        files['/usr/local/bin/forgejo-pull-hook'] = {
            'source': 'usr/local/bin/forgejo-pull-hook',
            'mode': '0755',
        }
        files['/etc/webhook.conf'] = {
            'content': json.dumps([{
                'id': 'forgejo-pull',
                'execute-command': '/usr/local/bin/forgejo-pull-hook',
                'command-working-directory': '/var/lib/forgejo-pull',
                'pass-arguments-to-command': [
                    # Forgejo sends an empty X-Forgejo-Event for package events and
                    # the real name in X-Forgejo-Event-Type; the script takes either.
                    {'source': 'header', 'name': 'X-Forgejo-Event-Type'},
                    {'source': 'header', 'name': 'X-Forgejo-Event'},
                    {'source': 'entire-payload'},
                ],
                'trigger-rule': {'match': {
                    'type': 'payload-hmac-sha256',
                    # same value goes into the org webhook in Forgejo:
                    #   bw debug -c "print(repo.vault.password_for('forgejo_webhook_<node>').value)"
                    'secret': repo.vault.password_for(f'forgejo_webhook_{node.name}').value,
                    'parameter': {'source': 'header', 'name': 'X-Forgejo-Signature'},
                }},
            }], indent=2) + '\n',
            'owner': 'root',
            'group': 'root',
            'mode': '0600',
            'triggers': ['svc_systemd:webhook:restart'],
        }
        files['/etc/systemd/system/webhook.service.d/override.conf'] = {
            'source': 'etc/systemd/system/webhook.service.d/override.conf',
            'triggers': ['action:forgejo_daemon_reload', 'svc_systemd:webhook:restart'],
        }
        svc_systemd['webhook'] = {
            'needs': [
                'pkg_apt:webhook',
                'file:/etc/webhook.conf',
                'file:/etc/systemd/system/webhook.service.d/override.conf',
                'file:/usr/local/bin/forgejo-pull-hook',
                'file:/usr/local/bin/forgejo-pull',
                'file:/etc/systemd/system/forgejo-pull@.service',
            ],
        }

    for name, target in cfg.get('targets').items():
        env = {'TYPE': target['type']}
        if target['type'] == 'static':
            env.update(PACKAGE=target['package'], DEST=target['dest'])
            # The site dir may already be managed elsewhere (nginx bundle creates vhost
            # roots), so don't claim it; just make sure the puller owns it.
            actions[f'forgejo_pull_own_{name}'] = {
                'command': f'mkdir -p {target["dest"]} && chown -R {puser}:{puser} {target["dest"]} && chmod 755 {target["dest"]}',
                'unless': f'test -d {target["dest"]} && test -z "$(find {target["dest"]} ! -user {puser} -print -quit)"',
                'needs': user_needs,
                'needed_by': [f'svc_systemd:forgejo-pull@{name}.timer'],
            }
            # Atomic deploys: nginx serves <dest>/current, the puller switches that symlink.
            current = f'{target["dest"]}/current'
            nginx_roots = {v.get('root') for v in node.metadata.get('nginx/sites', {}).values()}
            actions[f'forgejo_pull_bootstrap_{name}'] = {
                'command': f'install -d -o {puser} -g {puser} -m 0755 {target["dest"]}/releases '
                           f'&& ln -s . {current} && chown -h {puser}:{puser} {current}',
                'unless': f'test -L {current}',
                'needs': [f'action:forgejo_pull_own_{name}'],
                'needed_by': [f'svc_systemd:forgejo-pull@{name}.timer']
                             + ([f'directory:{current}'] if current in nginx_roots else []),
            }
        elif target['type'] == 'git':
            env.update(
                REPO=target['repo'],
                DIR=target['dir'],
                BRANCH=target.get('branch', 'master'),
                COMPOSE=str(target.get('compose', False)).lower(),
                REQUIRE_CI=str(target.get('require_ci', True)).lower(),
            )
            if target.get('env'):
                # Secrets for the stack itself, read by docker compose from <dir>/.env.
                # Must be git-ignored in the repo; the puller never resets untracked files.
                files[f'{target["dir"]}/.env'] = {
                    'content': ''.join(f'{k}={v}\n' for k, v in sorted(target['env'].items())),
                    'owner': 'root',
                    'group': 'root',
                    'mode': '0600',
                }
        else:
            raise BundleError(f"{node.name}: forgejo/pull/targets/{name}: unknown type {target['type']}")

        files[f'/etc/forgejo-pull/{name}.env'] = {
            'content': ''.join(f'{k}={v}\n' for k, v in env.items()),
            'owner': 'root',
            'group': puser,
            'mode': '0640',
            'needs': ['directory:/etc/forgejo-pull'],
        }
        svc_systemd[f'forgejo-pull@{name}.timer'] = {
            'enabled': bool(cfg.get('fallback_timer')),
            'running': bool(cfg.get('fallback_timer')),
            'needs': [
                'file:/usr/local/bin/forgejo-pull',
                'file:/etc/forgejo-pull/forgejo.env',
                f'file:/etc/forgejo-pull/{name}.env',
                'file:/etc/systemd/system/forgejo-pull@.service',
                'file:/etc/systemd/system/forgejo-pull@.timer',
                'pkg_apt:curl',
                'pkg_apt:jq',
                'pkg_apt:rsync',
            ],
        }
