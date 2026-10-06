# forgejo

Forgejo server and/or Actions runner, selected with `forgejo.roles`.

```python
'bundles': ['forgejo'],                 # runner role also needs 'docker'
'metadata': {
    'forgejo': {
        'roles': ['server'],            # or ['runner']
        'server': {
            'domain': 'git.example.org',                       # required
            'reverse_proxy': '192.0.2.10/32',                  # required, or:
            # 'reverse_proxy_node': {'node': 'www', 'interface': 'eth1'},
            'mirror_allowed_domains': 'github.com',            # optional, see metadata.py
        },
    },
},
```

`files/` holds upstream's own files for the pinned versions, with metadata
substituted into the handful of lines that differ: `custom/conf/app.example.ini`,
the runner's `config.example.yaml`, and both `contrib/` systemd units. Everything
that is still a comment in `app.ini` is the upstream default.

GitHub stays the source of truth: repos are pull mirrors (github.com only), synced
every `mirror_interval`. A sync is a push event and triggers `.forgejo/workflows/*.yml`,
which GitHub ignores. Job secrets (e.g. the deploy key for www) are Actions secrets.

## One-time steps after `bw apply`

Server: add authentik under Site Administration -> Authentication Sources (OAuth2,
OpenID Connect, client id `forgejo`, discovery URL from authentik), then log in
through it. The first account becomes administrator.

Runner, on the VM, with a token from Site Administration -> Actions -> Runners:

    sudo -u runner /usr/local/bin/forgejo-runner register --no-interactive \
      --instance https://<domain> --token <token> \
      --name runner --labels docker:docker://docker.io/library/debian:bookworm
    sudo systemctl start forgejo-runner

## Upgrading

Bump `version` and `sha256` in `metadata.py`, then diff the new upstream
`app.example.ini` / `config.example.yaml` against `files/` and carry over changes.
