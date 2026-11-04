# Organization MCP service

This separate addon expands the existing default-deny pilot only when an
administrator selects organization mode. Installing it preserves pilot policy.
Only organization1 and the existing bridge company may be configured. It does
not create or impersonate a PE-linked identity or bypass the native SSO fence.

The local service account has40 reviewed business models and24 built-in MuK
tools, with model/field/record/command/action checks. No technical ORM, identity
administration, DB-code tools, caller context or arbitrary public methods.
Existing native Odoo ACLs continue to apply. Company is fixed server-side;
company2, private projects, private calendars, user profiles and PE-managed
contacts are excluded. Existing contacts are not automatically published to MCP.
New business contacts are explicitly marked as shared and fixed to company1.
Global reference models get an actual organization column and native company
restriction; existing global rows are readable but not editable through MCP.

Continuous access uses a random hashed server key without expiry, encrypted with
DPAPI on the desktop, until explicitly revoked or authority becomes invalid.
Revocation is irreversible; user archive revokes all keys even after reenable.
Every request checks account/company/role/feature state. A5minute authority cron
records status and permanently revokes keys on invalid state. File links remain
short-lived/one-use and are bound to the creating key, including revocation.
Audit bodies remain omitted. Desktop/operator monitoring is separate from cron.

Business methods are a finite allowlist: record archive/unarchive, selected CRM,
sales and invoice actions. Printing is limited to sale/invoice reports. Accounting
posting still needs a correctly configured chart/journals/taxes; installing this
addon does not choose fiscal settings, charge a provider or certify accounting.
External messages or real business effects require an actual user task; acceptance
uses synthetic records and internal notes, not unsolicited communications.

Release: exact CI -> verified backup/restored clone -> real MCP read/write and
negative/lifecycle tests -> guarded promotion -> identity/policy provisioning ->
encrypted client update -> real public SDK/ordinary admin/SSO-entry checks ->
post-configuration backup. No owner SSO acceptance without actual owner login.
Neutralization disables the feature, keys and transfer links. After any restore,
keep MCP disabled pending revocation/policy reconciliation. Do not restore PE DB.
