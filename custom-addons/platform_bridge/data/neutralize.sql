UPDATE pe_bridge_config SET enabled=FALSE, writes_enabled=FALSE;
UPDATE res_users SET pe_contacts_grant=NULL WHERE pe_subject IS NOT NULL;
