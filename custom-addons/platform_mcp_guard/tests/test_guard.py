from datetime import timedelta
from unittest.mock import patch
import json

from odoo import fields
from odoo.exceptions import AccessError,AccessDenied,ValidationError
from odoo.tests import TransactionCase,tagged
from odoo.addons.muk_mcp.tools.exception import MCPScopeDenied


@tagged('post_install','-at_install')
class TestMCPGuard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        quiet=dict(tracking_disable=True,mail_create_nolog=True,mail_create_nosubscribe=True,mail_auto_subscribe_no_notify=True,no_reset_password=True)
        cls.config=cls.env.ref('platform_mcp_guard.main')
        cls.config.write({'enabled':False})
        cls.reader=cls.env['res.users'].sudo().with_context(**quiet).create(dict(name='Synthetic MCP reader',login='mcp-guard-fixture-reader',
            group_ids=[fields.Command.set([cls.env.ref('base.group_user').id,cls.env.ref('platform_mcp_guard.group_reader').id])],
            company_id=cls.env.company.id,company_ids=[fields.Command.set(cls.env.company.ids)]))
        cls.project=cls.env['project.project'].sudo().with_context(**quiet).create(dict(name='[נתוני בדיקה] MCP fixture',company_id=cls.env.company.id,privacy_visibility='employees',user_id=False))
        cls.task=cls.env['project.task'].sudo().with_context(**quiet).create(dict(name='[בדיקה] Allowed fixture',project_id=cls.project.id,user_ids=[]))
        cls.foreign=cls.env['project.task'].sudo().with_context(**quiet).create(dict(name='[בדיקה] Outside fixture scope',project_id=cls.project.id,user_ids=[]))
        cls.config.write(dict(user_id=cls.reader.id,project_id=cls.project.id,task_ids=[fields.Command.set(cls.task.ids)],enabled=True))
        cls.allow=cls.env['muk_mcp_access.model'].sudo().search([('model_name','=','project.task')],limit=1)
        data=dict(model_id=cls.env['ir.model']._get('project.task').id,allow_read=True,allow_write=False,domain=repr([('project_id','=',cls.project.id)]))
        if cls.allow:cls.allow.write(data)
        else:cls.allow=cls.env['muk_mcp_access.model'].sudo().create(data)

    def setUp(self):
        super().setUp()
        # Vendor audit uses an independent cursor; no uncommitted fixture FK waits.
        p=patch.object(type(self.env['muk_mcp.log']),'log',lambda *args,**kwargs:None)
        p.start();self.addCleanup(p.stop)

    def call(self,name='search_read',arguments=None):
        env=self.env(user=self.reader)
        return env['muk_mcp.tool']._call_result(name,arguments if arguments is not None else dict(model='project.task',fields=['id','name']),env,enforce_scope='read')

    def key(self):
        return self.env['muk_mcp.key'].with_user(self.reader)._generate('Fixture read','read',30)

    def test_only_three_read_tools_advertised(self):
        tools=self.env['muk_mcp.tool'].get_tools('mcp')
        self.assertEqual({t['name'] for t in tools},{'search_read','read_records','search_count'})
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in tools))
        self.assertTrue(all('context' not in t['inputSchema']['properties'] for t in tools))

    def test_read_scoped_record_and_no_foreign_search(self):
        result=self.call();self.assertFalse(result.get('isError'),result)
        rows=json.loads(result['content'][0]['text'])
        self.assertEqual([r['id'] for r in rows],self.task.ids)

    def test_direct_foreign_record_denied(self):
        self.assertTrue(self.call('read_records',dict(model='project.task',ids=self.foreign.ids,fields=['id']))['isError'])

    def test_other_models_metadata_and_mutation_tools_denied(self):
        for name,args in [('search_read',dict(model='res.partner',fields=['name'])),
                ('create_records',dict(model='project.task',values={'name':'NO'})),('call_method',{}),('system_info',{}),('list_models',{}),('read_resource',{})]:
            with self.subTest(name=name):self.assertTrue(self.call(name,args)['isError'])

    def test_closed_fields_context_domain_and_paging(self):
        for patch_data in [{'fields':['description']},{'context':{'sudo':True}},{'limit':0},{'limit':21},{'offset':-1},
                {'domain':[['partner_id.name','=','private']]},{'order':'id; select 1'},{'extra':'NO'}]:
            with self.subTest(data=patch_data):
                self.assertTrue(self.call(arguments=dict(model='project.task',fields=['id'],**patch_data) if 'fields' not in patch_data else dict(model='project.task',**patch_data))['isError'])

    def test_empty_or_archived_allowlist_denies(self):
        self.allow.write({'active':False})
        self.assertFalse(self.allow._is_model_allowed('project.task'))
        self.assertTrue(self.call()['isError'])

    def test_write_allowlist_entry_rejected(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():self.allow.write({'allow_write':True})

    def test_disabled_feature_and_other_reader_deny(self):
        self.config.write({'enabled':False})
        self.assertTrue(self.call()['isError'])
        self.assertFalse(self.env['muk_mcp.key'].authenticate('not-real'))

    def test_incomplete_enable_and_real_data_project_denied(self):
        self.config.write({'enabled':False})
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():self.config.write({'task_ids':[fields.Command.clear()],'enabled':True})
        self.project.name='Real business'
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():self.config.write({'enabled':True})

    def test_default_read_and_automatic_expiry(self):
        key,token=self.key()
        self.assertEqual(key.scope,'read')
        self.assertLessEqual(key.expires_at,fields.Datetime.now()+timedelta(days=7,seconds=5))
        self.assertEqual(self.env['muk_mcp.key'].authenticate(token),key)
        key.write({'expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        self.assertFalse(self.env['muk_mcp.key'].authenticate(token))

    def test_write_key_and_long_expiry_denied(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():self.env['muk_mcp.key'].with_user(self.reader)._generate('No write','write',30)
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():self.env['muk_mcp.key'].sudo().create(dict(name='No long key',user_id=self.reader.id,key_hash='f'*64,expires_at=fields.Datetime.now()+timedelta(days=8)))

    def test_revoke_does_not_resurrect_and_expiry_not_extended(self):
        key,token=self.key();key.write({'active':False})
        self.assertFalse(self.env['muk_mcp.key'].authenticate(token))
        with self.assertRaises(AccessError):key.write({'active':True})
        with self.assertRaises(AccessError):key.write({'expires_at':key.expires_at+timedelta(seconds=1)})
        with self.assertRaises(AccessError):key.write({'scope':'write'})

    def test_user_archive_revokes_key(self):
        key,token=self.key();self.reader.active=False
        self.assertFalse(self.env['muk_mcp.key'].authenticate(token))
        self.reader.active=True
        self.assertFalse(self.env['muk_mcp.key'].authenticate(token))

    def test_reader_has_no_normal_login_or_native_task_write(self):
        with self.assertRaises(AccessDenied):self.reader.with_user(self.reader)._check_credentials({'type':'password','password':'irrelevant'},{'interactive':True})
        with self.assertRaises(AccessError):self.task.with_user(self.reader).write({'name':'NO'})
        self.assertFalse(self.reader.has_group('base.group_system'))
        self.assertFalse(self.reader.has_group('project.group_project_user'))

    def test_record_scope_changes_invalidate_access_context(self):
        model=self.env['project.task'].with_user(self.reader)
        self.assertIn(self.task.id,model.search([]).ids)
        self.assertNotIn(self.foreign.id,model.search([]).ids)
        self.config.write({'task_ids':[fields.Command.set(self.foreign.ids)]})
        self.assertNotIn(self.task.id,model.search([]).ids)
        self.assertIn(self.foreign.id,model.search([]).ids)

    def test_no_db_tool_can_shadow_builtin(self):
        self.env['muk_mcp.tool'].sudo().create(dict(name='search_read',description='Synthetic shadow attempt',code="result = 'NO'",category='read'))
        self.assertTrue(self.call()['isError'])
