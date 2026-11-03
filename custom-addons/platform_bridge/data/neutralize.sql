UPDATE pe_bridge_config SET enabled=FALSE, writes_enabled=FALSE;
UPDATE res_users SET pe_contacts_grant=NULL WHERE pe_subject IS NOT NULL;
UPDATE ir_cron SET active=FALSE WHERE id IN (SELECT res_id FROM ir_model_data WHERE module='platform_bridge' AND name='sync_cron' AND model='ir.cron');
