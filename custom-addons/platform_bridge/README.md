# Platform Engineer bridge (TEST)

This optional addon is disabled by default. Its only enrolled client is
`odoo20-test`, with a fixed HTTPS callback and an explicit organization binding.
The current deployment is for organization 1. Separate databases for future
organizations are a design boundary, not an implemented provisioning feature.

## Identity and access

The existing Platform login and MFA authorize a short-lived, single-use S256
code. Odoo exchanges that code server to server for separate opaque login and
contact grants. This is a private, purpose-bound protocol, not an OIDC provider.
No Platform password is copied. Local Odoo administrator login is preserved.
Accounts bind only by stable organization/user subject, never by email or name.
New linked users receive ordinary contact access, without administrator roles.

A dedicated administrator-only, CSRF-protected Platform profile form controls
access. Every authenticated HTTP request, incoming websocket event and outbound
websocket data frame revalidates Platform authority. Revocation, account disable,
organization or authentication epoch changes invalidate access. A later enable
does not revive an earlier generation of grants or sessions. Provider or network
failure denies linked access. Secrets and contact grants are server-only fields.

## Contacts and profiles

Private contacts remain visible only to their owner. Organization contacts are
visible within the enrolled organization. Platform's current read/write manifest
also fences every Odoo contact operation. Cross-organization and separately
shared contacts are not exported. Native administrator powers do not permit
exporting other users' private contacts.

Six business fields synchronize: first name, last name, email, phone, mobile
phone and company. Names in the user profile can synchronize both ways.
Platform remains authoritative for account email/phone verification, credentials,
roles, activation, organization membership and the access checkbox. Odoo account
creation does not provision a new Platform identity. Sharing/ownership changes
use Platform's existing controls; Odoo cannot broaden them.

Three-way reconciliation preserves independent edits and retains all three
versions of conflicts. Unclassified duplicate candidates require review and are
never automatically merged. Paused/merged/archived records are held; absence
from a page never deletes data. Unpublished private edits block broader sharing.
New contacts created by linked Odoo users are exported as owned private records.
Opt-out metadata is carried; the bridge sends no messages or invitations.

Every outbound mutation has an immutable original operation ID/body, persisted
before transfer, and an atomic Platform receipt. An unknown outcome is recovered
using that ID and payload digest before any new intent. Version mismatches require
fresh reconciliation. No timestamp chooses a winner. Review records are read-only;
resolve business edits in the authoritative contact editors and review held
operations before resuming them.

## Deployment and rollback

Install and test `platform_bridge` through the TEST release controller on a
neutralized restored database before promotion. Feature enable, writes and cron
are separate switches. Start with a scoped dry run and keep writes disabled until
its counts and review cases are checked. Cron is inactive in the source defaults.
Neutralization disables the bridge, cron and grants.

Deploy the matching private Platform package and additive access/receipt schema
using verified native preimages and the shared deployment/call guards. Preserve
native authentication code and unrelated changes. Back up both sides before
admission. Never restore the shared Platform database merely to roll this addon
back: disable the bridge first and retain access generations and receipts.

Automated isolated tests prove their fixture behavior. They do not constitute a
successful real owner's login/MFA or real contact synchronization acceptance.
