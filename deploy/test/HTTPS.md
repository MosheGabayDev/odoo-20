# Dedicated Odoo TEST HTTPS origin

Public URL: `https://odoo.test.ai-data-platform.com`.

The existing Platform Cloudflare tunnel and port 5000 routing remain independent.
This hostname uses a DNS-only A record to the TEST elastic IP, a dedicated
Let's Encrypt certificate obtained through a DNS-01 challenge, and the existing
nginx master's graceful reload. Only TCP 443 is opened for this service; neither
the application backend, PostgreSQL nor port 80 is published.

The zone's API credential permits DNS operations but cannot read Cloudflare SSL
certificate packs. No broader credential or paid certificate feature is used.
The DNS token is encrypted during transfer and stored root-only outside Git at
`/opt/odoo20-test/https/secrets/cloudflare.ini`.

## Application and proxy

The private host `release-config.json` sets `public_url` to the public origin and
`websocket_port` to 18072. Prepared previews ignore these public settings and
remain cron-disabled, unpublished and without outbound networking.

Published releases enable Odoo `proxy_mode`, two HTTP workers and gevent on
8072. Only host loopback ports 18069/18072 are mapped. nginx routes normal HTTP
to 18069 and `/websocket` to 18072, normalizes forwarded headers from the actual
client, forces secure session-cookie attributes, blocks the database manager,
limits login requests and rejects TLS handshakes for unknown hostnames.

`web.base.url` is set with Odoo 20's typed `set_str` API and
`web.base.url.freeze` with `set_bool`, preventing an internal health-check login
from replacing the public URL. These settings are in PostgreSQL and follow
the matched database snapshot across updates.

The nginx fragment is included once inside the existing `/etc/nginx/nginx.conf`
`http {}` context. Installation backs up the complete preimage, holds the shared
deployment lock, runs the private TEST/call guard, verifies `nginx -t`, and uses
`reload` rather than restart. Other routes are not rewritten. Its tracked source
is `nginx.odoo-https.conf`; the deployed fragment is `/etc/nginx/odoo20-https.conf`.

## Certificates and persistence

Certbot runs in the official `certbot/dns-cloudflare` image pinned by digest in
`https/certbot-image.txt`. Host directories persist certificate account/key,
renewal settings and private DNS credentials. `odoo20-cert-renew.timer` checks
twice daily. `certbot-renew.sh` reloads nginx only when the certificate changes.
The same command with `--dry-run` tests renewal against ACME staging without
replacing the live certificate. Certificate files are root-only.

Public-release backups include the private deployment configuration and a
KMS-encrypted `https-state.tar.gz` containing the certificate and renewal
credential. This archive is confidential and is never a repository artifact.

## Release compatibility and recovery

HTTPS-capable images carry `io.odoo.release.public-proxy=1`. The controller
rejects an older incompatible image before stopping the current public release.
The first rollout establishes a compatible private release before publishing a
second release of the same image; its immediate rollback target therefore also
supports the public proxy. Older pre-HTTPS images can still be restored privately
using their matched data, followed by a reviewed HTTPS-capable forward build.

DNS, the single 443 security-group rule, nginx inclusion and renewal units are
host infrastructure. They persist across application releases and must be
recreated explicitly after complete host loss. `recover` restores the recorded
application state; it does not silently rewrite cloud DNS or firewall rules.

The HTTPS origin is directly served by the TEST host, so Cloudflare's proxy WAF
does not process this hostname. TLS, nginx safeguards and Odoo authentication
protect the service. Existing Cloudflare-proxied Platform hostnames keep their
own routes. Public reachability, TLS hostname validation, login, assets, secure
cookies, websocket upgrade and existing Platform health must be verified after
installation. No real host reboot is part of publication.

References: [Odoo deployment](https://www.odoo.com/documentation/20.0/administration/on_premise/deploy.html),
[Certbot DNS-Cloudflare](https://certbot-dns-cloudflare.readthedocs.io/en/stable/),
[Cloudflare multi-level certificate coverage](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/).
