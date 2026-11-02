# Odoo 20 on Platform Engineer TEST

This isolated deployment runs the source commit
`0cdbc41e6e1588ba5755df944ba2407bd4ed888f` from `MosheGabayDev/odoo-20`.
It does not reuse the Platform Engineer Python environment, PostgreSQL databases,
container networks, service units or public proxy routes.

The stack lives in `/opt/odoo20-test` on `i-0bc4b89a6ff80ae54`.
Its Compose project is `odoo20-test`. PostgreSQL 16 has its own volume, an internal
network, no published port and a restricted application role. The application
runs as UID 10001 with a read-only container root, persistent filestore, limited
CPU/memory, no Linux capabilities and loopback-only HTTP on port 18069.

## Initial setup

Validate host capacity, shared deployment lock, idle voice state, backup status,
existing service health and all name/port collisions before creating resources.
The bundle's files must match `deployment.sha256`. `.env` records the immutable
PostgreSQL image digest. Build and validate the Odoo image before initialization.

Generate four independent random secrets in `secrets/` on the target:
`postgres_password`, `odoo_db_password`, `odoo_master_password`,
`odoo_admin_password`. Keep the directory root-owned mode 0700. Mounted secret
files must be readable by the container users (0444 inside that protected parent).
Never put secret values in Compose environment variables, logs, shell arguments
or source control.

Start only `db`, wait for health, then run `docker compose run --rm --no-deps -T
app bootstrap`. Bootstrap refuses a nonempty database, installs base and CRM
without demo data, and sets a random administrator password while no HTTP server
is running. Start `app` only after successful initialization. Database listing
and the public database manager are disabled; the database filter is exact.

## Access

Use AWS Systems Manager port forwarding from local port 18069 to the host's
loopback port 18069. Open `http://localhost:18069`. The server connection is
encrypted by SSM; HTTP stays on loopback. The connection requires authorized
AWS credentials and a running local tunnel. No public DNS, inbound port or
Platform Engineer route is created. Login is `admin`; retrieve its password
through an authorized private server session from `secrets/odoo_admin_password`.

## Backup and restore

`bash /opt/odoo20-test/backup.sh` stops only this Odoo application, captures a
PostgreSQL custom dump and the matching filestore, checks both archives and
uploads private objects to the existing TEST backup bucket under `odoo20/`.
It restarts Odoo if Odoo was running before the backup. Secrets are separate;
retain them privately with the server recovery material. The daily Odoo backup
timer must report success; existing Platform Engineer backup jobs are unchanged.

For recovery, keep the original volumes and restore into a separate fresh stack
with the same source and image. Verify SHA256SUMS, restore the PostgreSQL dump
with `pg_restore` into the dedicated database, restore the matching filestore
as UID 10001, provision the private secrets, then verify login and CRM before
switching access. Never run `compose down -v`, database drops, volume pruning or
restore over an existing database as routine deployment steps.

## Operations and limits

`docker compose ps` and the application health check must be healthy. Save the
deployed image ID, source commit and manifests with each backup. To roll back a
code-only update, stop only `app` and select the preceding image after checking
database compatibility. Retain data volumes and restore into a separate stack
if an update changes schema.

This is a TEST instance on a Spot host. It stops when the host stops and access
requires the local tunnel. Public HTTPS, outgoing mail, company configuration,
additional ERP modules, Hebrew RTL assets and PDF report rendering require
separate configuration and acceptance. The source build does not include a
patched wkhtmltopdf binary or rtlcss, so those features are not certified here.
