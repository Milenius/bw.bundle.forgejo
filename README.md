# forgejo

Forgejo server, Actions runner and/or artifact puller, selected with `forgejo.roles`.

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

Server: without any account you cannot reach the admin settings, so create a
temporary local admin on the VM, configure authentik with it, then remove it:

    sudo -u git /usr/local/bin/forgejo --config /etc/forgejo/app.ini \
      admin user create --admin --username bootstrap --random-password --email <you>

Log in as `bootstrap`, add authentik under Site Administration -> Authentication
Sources (OAuth2, OpenID Connect, client id `forgejo`, client secret and discovery
URL from authentik). Log in once via authentik, make that account admin as
`bootstrap`, then delete `bootstrap`.

Runner: in Forgejo, Site Administration -> Actions -> Runners -> Create new runner.
It shows a UUID and a token once. Put them into the runner node:

    'forgejo': {
        'roles': ['runner'],
        'runner': {
            'connections': {
                'gipsburg': {
                    'url': 'https://git.example.org',
                    'uuid': '<uuid from Forgejo>',
                    'token': vault.decrypt('<bw debug -c "print(repo.vault.encrypt(\'<token>\'))">').value,
                },
            },
        },
    },

`bw apply` renders it into `config.yaml` and starts the service; the runner shows
up as Idle. (`forgejo-runner register` is deprecated since runner v13.)

Webhook deliveries to private addresses need `server.webhook_allowed_hosts` (Forgejo's
`webhook.ALLOWED_HOST_LIST`, upstream default `external`), e.g. `'10.190.5.0/24'`.

## Role `pull`: deploy by fetching, not by pushing

Hosts deploy themselves: a Forgejo org webhook tells the host that a package was
uploaded or a workflow run succeeded, the host runs `forgejo-pull` for the matching
target and fetches from Forgejo with a read-only token. CI never touches hosts.
A daily timer is the fallback for missed webhook deliveries (`fallback_timer`, `interval`).

- `static`: newest version of a generic package (published by the shared `publish-site`
  action) is checksum-verified and rsynced into the site directory.
  Rollback: delete the bad package version in Forgejo.
- `git`: a checkout follows a branch of the repo's Forgejo mirror. With `require_ci` a
  commit is only deployed once the latest run of every workflow for it is `success`, so a
  test-build workflow gates the deploy (manual re-runs count). The pull user's org team
  needs Code: Read and Actions: Read for this. Then `docker compose up -d --build` if `compose`. Only tracked files are reset,
  data directories like `pb_data/` are never touched, local modifications make it refuse.
  Rollback: `git checkout <older commit>` in the directory. Needs `user: 'root'`.

```python
'forgejo': {
    'roles': ['pull'],
    'pull': {
        'url': 'https://git.example.org',
        'owner': 'gipsburg',
        'token': vault.decrypt('...').value,                 # ci-pull token for this host: read:package, read:repository
        'webhook': {'from_node': 'de.gipsburg.infra.git'},   # firewall: only Forgejo may call it
        'targets': {
            'doku': {'type': 'static', 'package': 'doku.intern.gipsburg.de', 'dest': '/var/www/doku.intern.gipsburg.de'},
            'netzkasse': {'type': 'git', 'repo': 'verwaltung-netzwerkkasse', 'dir': '/var/www/verwaltung-netzwerkkasse',
                          'compose': True, 'require_ci': True,
                          'env': {'PB_ENCRYPTION_KEY': vault.password_for('netzkasse_pb_key', length=32).value}},
        },
    },
},
```

### New host with the pull role (once per host)

1. Token: `forgejo admin user generate-access-token --username ci-pull --token-name <host> --scopes read:package,read:repository`, vault-encrypt into the node.
2. If the host reaches Forgejo through the www proxy, add its IP to the `git.intern` vhost allowlist.
3. `bw apply`.
4. Org webhook in Forgejo: organisation -> Settings -> Webhooks -> Add -> Forgejo.
   URL `http://<host's internal IP>:9000/hooks/forgejo-pull` (the firewall only admits the
   Forgejo node's address, so use the address the Forgejo VM reaches directly, not a
   public name), POST, JSON, secret from
   `bw debug -c "print(repo.vault.password_for('forgejo_webhook_<node name>').value)"`,
   events: *Package* and *Action run succeeded*.

### New repo / site (no host or Forgejo settings needed)

Mirror the repo into the org, add a target to the host's node, `bw apply`, and give the
repo a workflow that ends in `publish-site` (static) or a test-build (git). The org
webhook already covers every repo and package in the org.

## Backup

Back up `/var/lib/forgejo` (restic in this repo: `restic.backup_folders` in the node).
The server role writes a consistent copy of the SQLite database to
`data/forgejo.db.snapshot` every night (`db_snapshot_time`, default 04:30); restore
that file as `data/forgejo.db`, not the live one. `SECRET_KEY` is vault-derived, so a
rebuilt host decrypts the restored database without extra steps.

## Upgrading

Bump `version` and `sha256` in `metadata.py`, then diff the new upstream
`app.example.ini` / `config.example.yaml` against `files/` and carry over changes.
