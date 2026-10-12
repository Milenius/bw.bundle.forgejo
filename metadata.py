defaults = {
    'forgejo': {
        # 'server' and/or 'runner', set per node.
        'roles': [],
        'server': {
            # https://codeberg.org/forgejo/forgejo/releases
            'version': '16.0.5',
            'sha256': '2c548bf1bfa09a441a3a674b1dd4959d77576816ad50534c23496908a3ea5a19',
            'home': '/var/lib/forgejo',
            'http_port': 3000,
            # Required in the node: public name served by your reverse proxy.
            'domain': None,
            # Name clients use for git over ssh; the host itself by default.
            'ssh_domain': node.hostname,
            # Who may reach http_port and is trusted for X-Forwarded-For. Either a
            # literal CIDR in 'reverse_proxy', or let the reactor derive it from
            # another node: {'node': '<node name>', 'interface': 'eth1'}.
            'reverse_proxy': None,
            'reverse_proxy_node': None,
            # Nightly consistent copy of the SQLite db (data/forgejo.db.snapshot) for
            # backups; schedule it shortly before the backup runs. None disables it.
            'db_snapshot_time': '*-*-* 04:30:00',
            # Pull mirrors: sync interval and allowed source domains ('' = upstream default).
            'mirror_interval': '1m',
            'mirror_allowed_domains': '',
            # Where Forgejo may deliver webhooks; upstream default 'external' refuses
            # private addresses. Set e.g. 'private' or '10.190.5.0/24' for pull hosts.
            'webhook_allowed_hosts': 'external',
        },
        'runner': {
            # https://code.forgejo.org/forgejo/runner/releases
            'version': '13.2.0',
            'sha256': 'fadaec897f5e6641c363f87ecaf75f866f99c81b7097ec6b433e4c48123fcad1',
            'home': '/var/lib/forgejo-runner',
            # 2g is too little for kaniko + a JS build (kaniko snapshots layers in memory);
            # the job thrashes at the limit instead of failing.
            'container_options': '--security-opt=no-new-privileges --memory=6g --pids-limit=1024',
            # Jobs run in containers only; the image is the default for `runs-on: docker`.
            # Needs node: the standard actions (checkout etc.) are JavaScript.
            'labels': ['docker:docker://docker.io/library/node:22-bookworm'],
            # Required in the node. One entry per Forgejo instance; uuid and token come
            # from "Create new runner" in Forgejo (Site Administration -> Actions -> Runners):
            # {'gipsburg': {'url': 'https://...', 'uuid': '...', 'token': vault.decrypt('...').value}}
            'connections': {},
        },
        # Role "pull": this host fetches artifacts from Forgejo on a timer. CI only
        # publishes; no deploy keys or CI credentials on the host.
        'pull': {
            'url': None,        # required, e.g. 'https://git.intern.gipsburg.de'
            'owner': None,      # required, org/user that owns the packages
            'token': None,      # required, read-only token: read:package (+ read:repository for git targets)
            'user': 'deploy',   # timer user; owns static dests. 'root' for git+compose targets
            # Deploys are triggered by Forgejo webhooks (package uploaded, workflow run
            # succeeded). The timer is only a fallback for missed deliveries.
            'fallback_timer': True,
            'interval': '1d',
            'webhook': {
                'enabled': True,
                'port': 9000,
                # who may call the webhook: the Forgejo node (resolved to its main IP) or a CIDR
                'from_node': None,
                'from': None,
            },
            # 'static': newest generic package <package> is unpacked into <dest>/releases/<version>
            #           and <dest>/current is switched atomically; set the nginx root to <dest>/current
            #   {'doku': {'type': 'static', 'package': 'doku.intern.gipsburg.de', 'dest': '/var/www/doku.intern.gipsburg.de'}}
            # 'git':    checkout <dir> follows <repo>'s <branch> in Forgejo (only CI-green commits
            #           when require_ci), then docker compose up -d --build if compose; needs user root.
            #           Optional 'env': {KEY: value} is written to <dir>/.env (0600) for the stack.
            #   {'netzkasse': {'type': 'git', 'repo': 'verwaltung-netzwerkkasse', 'dir': '/var/www/verwaltung-netzwerkkasse',
            #                  'compose': True, 'require_ci': True}}
            'targets': {},
        },
    },
    'apt': {
        'packages': {
            'git': {},
            'curl': {},
            'jq': {},
            'rsync': {},
            'sqlite3': {},
        },
    },
}


@metadata_reactor
def resolve_reverse_proxy(metadata):
    """Turn reverse_proxy_node into a /32 from that node's interface, so a changed
    address on the proxy node propagates here instead of going stale."""
    if 'server' not in metadata.get('forgejo/roles'):
        raise DoNotRunAgain

    ref = metadata.get('forgejo/server/reverse_proxy_node')
    if not ref:
        raise DoNotRunAgain

    proxy = repo.get_node(ref['node'])
    ip = proxy.metadata.get(f'interfaces/{ref["interface"]}/ip_addresses')[0]
    return {
        'forgejo': {
            'server': {
                'reverse_proxy': f'{ip}/32',
            },
        },
    }


@metadata_reactor
def add_server_iptables_rules(metadata):
    if 'server' not in metadata.get('forgejo/roles') or not node.has_bundle('iptables'):
        raise DoNotRunAgain

    cfg = metadata.get('forgejo/server')
    if not cfg.get('reverse_proxy'):
        return {}  # not resolved yet, bundlewrap calls us again

    return {
        'iptables': {
            'rules': [
                repo.libs.iptables.accept().chain('INPUT').tcp()
                    .source(cfg.get('reverse_proxy'))
                    .dest_port(str(cfg.get('http_port')))
                    .comment('forgejo http from reverse proxy'),
            ],
        },
    }


@metadata_reactor
def resolve_webhook_source(metadata):
    if 'pull' not in metadata.get('forgejo/roles'):
        raise DoNotRunAgain
    ref = metadata.get('forgejo/pull/webhook/from_node')
    if not ref:
        raise DoNotRunAgain
    src = repo.get_node(ref)
    iface = src.metadata.get('main_interface')
    ip = src.metadata.get(f'interfaces/{iface}/ip_addresses')[0]
    return {'forgejo': {'pull': {'webhook': {'from': f'{ip}/32'}}}}


@metadata_reactor
def add_pull_packages_and_firewall(metadata):
    if 'pull' not in metadata.get('forgejo/roles'):
        raise DoNotRunAgain
    hook = metadata.get('forgejo/pull/webhook')
    if not hook.get('enabled'):
        raise DoNotRunAgain
    result = {}
    if node.has_bundle('apt'):
        result['apt'] = {'packages': {'webhook': {}}}
    if node.has_bundle('iptables'):
        if not hook.get('from'):
            return result  # source not resolved yet, bundlewrap calls us again
        result['iptables'] = {
            'rules': [
                repo.libs.iptables.accept().chain('INPUT').tcp()
                    .source(hook.get('from'))
                    .dest_port(str(hook.get('port')))
                    .comment('forgejo-pull webhook from forgejo'),
            ],
        }
    return result
