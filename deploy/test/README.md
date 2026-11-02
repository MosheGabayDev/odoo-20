# Odoo TEST: GitHub → isolated rehearsal → explicit release

This deployment uses Docker built from this repository's exact Git commit, not
an unversioned official Odoo package. PostgreSQL and the filestore use independent
named volumes. Runtime configuration and four secret files live on the host.

## Daily workflow from Windows

Work in `C:\Projects\odoo-20`. Add custom modules under `custom-addons/`, including
XML/data/security declarations and module-specific tests. Increment the module
manifest version. Settings edited through the Odoo interface stay in PostgreSQL;
they are preserved in snapshots but do not automatically become Git files.

1. Commit exact owned files and push to the intended GitHub branch. The configured
   CI runs automatically for `deploy/test` and pull requests. Other branches need
   a pull request or an explicit workflow dispatch on that branch.
2. Read the exact 40-character SHA using `git rev-parse HEAD`.
3. Prepare a candidate with the helper. An example (replace SHA and module name):

```powershell
.\deploy\test\Invoke-OdooRelease.ps1 -Action Prepare -Commit SHA -Upgrade my_module -Test my_module
```

For a new module use `-Install my_module -Test my_module`. For code without a
module update, omit both module switches. `base`/`all` broad updates are blocked.
The operator must list affected modules accurately; Git cannot infer every schema
or XML change. Tests must actually report a nonzero number of tests and no failures.

4. Wait for the helper's `candidate-...` ID and successful GitHub CI for that exact
   SHA. Review the candidate's smoke/test logs privately on the host.
5. Explicitly promote that ID:

```powershell
.\deploy\test\Invoke-OdooRelease.ps1 -Action Promote -Candidate candidate-ID
```

No push deploys the server. GitHub Actions holds no AWS credentials and cannot
promote. The host controller checks the public GitHub CI result before promotion.
If the helper disconnects, resume observation using `-CommandId THE_ORIGINAL_ID`.
Never repeat a submission merely because its outcome is unknown.

The helper reads an ignored `connection.local.json` next to it, containing only
`instance_id` and `region`. AWS credentials remain in the existing local AWS
credential provider. Receipts go into the ignored `receipts/` directory.

## What prepare and promote do

`prepare` fetches the requested SHA from the fixed GitHub repository, checks the
checkout, builds an image using the configured Python base digest, and records its
immutable image ID. It archives the exact image to private KMS-encrypted S3.
It briefly stops only the current Odoo app for a consistent PostgreSQL/filestore
snapshot, then resumes it. The snapshot also includes private runtime secrets,
SHA256 manifests, configuration/business fingerprints and image recovery metadata.
Successful upload length and encryption are independently checked before marking
that snapshot complete.

A fresh Compose project restores that snapshot into new database and filestore
volumes. Preview containers have no host port, no outbound network, cron disabled
and Odoo database neutralization. Requested module installation/upgrades and tests
run there. Readiness checks cover administrator login, CRM, module state, role
restrictions, backend assets and disabled database management. Restored business
and settings signatures must match before migration; selected CRM/user/company
and business-attachment signatures must survive the module upgrade unchanged.
Intentional business-data migrations therefore require a separate reviewed plan.
Prepared preview containers and networks are removed after testing; named data
volumes, immutable images, snapshots and evidence are retained. Retired releases
are handled the same way after a successful cutover, preventing address-pool exhaustion.

`promote` rejects a stale candidate, obtains the shared Platform deployment lock
and the Odoo release/backup locks, checks the private host guard and protected
service health, then takes a NEW consistent snapshot while keeping old Odoo stopped.
This captures writes since prepare. It restores into another fresh project,
repeats migration and private smoke checks, and only then publishes loopback port
18069. The previous database and volumes are not overwritten or deleted.

Before exposure, failure automatically resumes the unchanged previous release.
Once exposure starts, failure NEVER silently restores older data: new writes may
already exist. Desired current state and transaction phases are fsynced on disk.
`recover` resumes the recorded current release after exposure, or returns to the
previous one before exposure. An interrupted operation blocks a new release until
its exact transaction is inspected/recovered.

## Status, backup and recovery

```powershell
.\deploy\test\Invoke-OdooRelease.ps1 -Action Status
.\deploy\test\Invoke-OdooRelease.ps1 -Action Backup
.\deploy\test\Invoke-OdooRelease.ps1 -Action Recover
```

Equivalent root commands on the TEST host are `python3
/opt/odoo20-test/release.py status|backup|recover`. The active stack is identified
by `release-state.json`; the old top-level `compose.yaml` is only the initial
baseline after the first promotion. Do not use it to operate a newer release.
The daily backup wrapper follows the current pointer automatically. Backup retry
and the boot-recovery unit are systemd services dedicated to Odoo.

All application and database containers use `restart: unless-stopped`. Recreating
a container retains its named volume; the immutable image supplies code, and
root-only host directories supply configuration/secrets. The boot unit starts the
persisted current stack and handles interrupted backup/cutover state. This is not
a promise of zero loss: host/volume loss can require the last completed snapshot,
and uncommitted PostgreSQL transactions can be rolled back after a crash.

## Rollback and data compatibility

Rollback creates another fresh stack using the exact earlier image AND its matched
pre-promotion database, filestore and secrets. It first archives the current live
state to KMS-encrypted S3 and retains its volumes. It does not attempt unsupported
backward SQL migrations or run old code on an upgraded live database.

Restoring the earlier snapshot hides business writes made after that snapshot.
Review this consequence and supply the CURRENT release ID explicitly:

```powershell
.\deploy\test\Invoke-OdooRelease.ps1 -Action Rollback -AcceptSnapshotRestore release-CURRENT-ID
```

Without that exact acknowledgement the operation fails before any change. If the
new writes must remain visible, prepare a forward fix or a separately validated
migration instead. The old/new retained stacks and pre-rollback archive preserve
recovery material; merging business changes is not automated.

For complete host loss, obtain the most recent S3 snapshot with `complete.json`,
verify its manifest SHA256 and every archive hash, load the image archive named in
its manifest, provision the same Python/controller configuration, and restore
into fresh volumes. `complete.json` is the upload completion barrier. Secret
archives are confidential and must remain in private KMS-protected storage.

## Safety and limits

The private root-owned configuration pins PostgreSQL and Python image digests,
backup location, access port, secrets path and the environment/call/health guard.
The controller does not restart Platform Engineer services or delete any volume.
Failed candidates/snapshots and image archives are retained for investigation;
retention/pruning is a separate deliberate operation. Low disk space holds work.
Do not use `docker system prune`, `compose down -v`, or a database drop to update.

APT/PyPI availability still affects a fresh build; keeping the exact deployed image
archive is the reproducible recovery path. A major Odoo/PostgreSQL version upgrade,
public HTTPS, browser tour tests, outgoing mail, Hebrew RTL assets and patched PDF
rendering require additional configuration/acceptance. Current smoke tests are not
the full upstream Odoo test suite. A real reboot affects other host services and
must be scheduled separately; container-crash and targeted recovery tests can run
in an isolated rehearsal project.

## Official references used

- [Odoo 20 testing/own CI](https://www.odoo.com/documentation/20.0/developer/tutorials/unit_tests.html)
- [Odoo staging against copied data](https://www.odoo.com/documentation/20.0/administration/odoo_sh/create_module.html)
- [Official Docker Odoo persistence/addons/secrets](https://github.com/docker-library/docs/blob/master/odoo/content.md)

These references inform the workflow; this server is self-hosted, not Odoo.sh.
## Repeat the isolated acceptance rehearsal

Install the tracked rehearse-pipeline.py next to the host controller. After one
prepared candidate exists, run python3 /opt/odoo20-test/rehearse-pipeline.py.
It creates unpublished sandbox projects and synthetic CRM/settings/attachment
data. It exercises failed and successful cutover, actual app/PostgreSQL process
crashes, recreation, targeted recovery, rollback refusal and acknowledged rollback,
and restores the pre-rollback archive to prove later writes remain recoverable.
It stops its sandbox containers and retains their data/evidence. It never reboots
the host. Read pipeline-proof-result.json for the actual acceptance result.
