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
            # Pull mirrors: sync interval and allowed source domains ('' = upstream default).
            'mirror_interval': '1m',
            'mirror_allowed_domains': '',
        },
        'runner': {
            # https://code.forgejo.org/forgejo/runner/releases
            'version': '13.2.0',
            'sha256': 'fadaec897f5e6641c363f87ecaf75f866f99c81b7097ec6b433e4c48123fcad1',
            'home': '/var/lib/forgejo-runner',
            'container_options': '--security-opt=no-new-privileges --memory=2g --pids-limit=512',
        },
    },
    'apt': {
        'packages': {
            'git': {},
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
