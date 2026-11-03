# Scoped TEST MCP

MuK upstream is pinned in deploy/test/vendor-addons.json, with license notices
included in the image. This addon narrows it without editing upstream source.

The default singleton is disabled. Enabling requires one active local non-admin
reader, the explicitly designated test project/company and1–20 named test tasks
without customer links. The account has no normal password login. It receives
only read grants for those tasks/project/stages, without Project User/Admin roles.
Existing local administrator and Platform-linked login checks are preserved.

Only search_read, read_records and search_count are advertised and callable.
Only project.task is allowed, with a closed set of basic fields, simple bounded
domains, maximum20records/page and no caller context or arbitrary method calls.
Resources/prompts/metadata tools and database-defined tool overrides are denied.
The MuK model allowlist also refuses empty policies and all write entries.

All keys default to read scope. Write keys are rejected, only the configured
reader may own keys, and expiry is checked on each request (maximum7days).
Expiry cannot be extended, revoked keys cannot be re-enabled, and disabling the
reader permanently revokes their old keys even after the account is re-enabled.
Rotation means generating a new key. Tokens are random and stored hashed in
Odoo. Audit preserves outcomes/IDs while omitting request/response data bodies.

Use only HTTPS /api/mcp on the existing hostname. No new public listener,
wildcard browser origin, OAuth registration or general business write is needed.
Store client keys encrypted outside source/config/output. Retain only encrypted
transport artifacts and public expiry/key-ID metadata. After restore or rollback,
disable MCP until current policy/key state is reconciled; neutralization disables
the feature and keys. Never restore the shared Platform database for this addon.

Release order: exact Git CI -> guarded fresh backup -> restored/neutralized
candidate plus existing bridge/tests/UI -> promote while disabled -> provision
the narrow account/policy and expiring key -> real SDK public positive/negative
checks -> encrypted client setup -> actual unchanged administrator login/WS.
An owner SSO login is a separate acceptance step when no owner session is available;
do not forge one or exempt it from fresh native authority.
