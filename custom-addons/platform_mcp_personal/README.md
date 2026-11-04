# Personal MCP

Disabled and unbound on install. Preparation stores an exact already verified PE
SSO user and a public RSA delivery key, without changing the active read pilot.
The role grants finite business permissions, never System/ERP or service-reader.

The human starts `/platform/login?purpose=mcp`, completes the existing fresh SSO,
then reviews a separate native continuous personal-business consent. PKCE, fixed
callback, server-session state/identity/delivery binding and five-minute intent
expiry precede exchange. Native grant purpose differs from login/contact grants.
Only that actual callback switches the feature, sets the40 model policies and
creates a personal write key. Native grants are encrypted with a domain-separated
key derived from the existing bridge client secret. Desktop delivery is RSA-OAEP
SHA256 encrypted to the prepared computer. No raw key is displayed in the browser.

Each authentication, operation and file transfer rechecks native account/org/
epoch/access version/consent. Operation result-publication rechecks happen inside
MuK's savepoint, so denied/changed authority rolls back Odoo DB writes and withholds
the response. Completed external effects cannot be undone by this fence. Technical
models, caller context, arbitrary methods and report templates remain closed.

Native contact scope is combined with own-private/shared-business rules; admin
status never authorizes another owner's private contacts/profile. The existing
browser/websocket SSO checks remain in effect. Ordinary SSO is not disabled by a
service-reader role. Local account archive permanently disables its keys. Native
consent revocation invalidates requests and unused file links even while a local
key's active flag is still true. Monitoring never auto-mints or extends grants.

Clone neutralization erases native-grant/delivery ciphertext and disables keys and
feature; a clone must not use or revoke an owner's real grant. No fiscal setup,
real financial posting, provider messaging or automatic contact writes are implied.
