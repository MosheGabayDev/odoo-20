import json
from datetime import timedelta
from unittest.mock import patch
from types import SimpleNamespace
from odoo import fields
from odoo.tests import TransactionCase,tagged
from odoo.exceptions import AccessError,AccessDenied,ValidationError
from ..policy import READ,WRITE,TOOLS,GLOBAL
from ..tools import QUIET
from .. import models as org_models


@tagged('post_install','-at_install')
class TestOrganizationMCP(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cfg=cls.env.ref('platform_mcp_guard.main').sudo()
        cls.cfg.write({'enabled':False,'access_mode':'organization','continuous':True})
        cls.company=cls.env.company
        cls.foreign_company=cls.env['res.company'].sudo().create({'name':'Synthetic foreign organization'})
        bridge=cls.env.ref('platform_bridge.main').sudo()
        bridge.write({'enabled':True,'org_id':1,'company_id':cls.company.id})
        cls.user=cls.env['res.users'].sudo().with_context(**QUIET).create(dict(name='Synthetic organization MCP',login='mcp-org-fixture',
            group_ids=[fields.Command.set(cls.env.ref('platform_mcp_organization.group_writer').ids)],company_id=cls.company.id,
            company_ids=[fields.Command.set(cls.company.ids)]))
        cls.cfg.write(dict(user_id=cls.user.id,company_id=cls.company.id,org_id=1,enabled=True))
        for model in READ:
            entry=cls.env['muk_mcp_access.model'].sudo().with_context(active_test=False).search([('model_name','=',model)],limit=1)
            data=dict(active=True,allow_read=True,allow_write=model in WRITE,domain=f"user._mcp_org_domain('{model}')")
            if entry:entry.write(data)
            else:cls.env['muk_mcp_access.model'].sudo().create(dict(data,model_id=cls.env['ir.model']._get(model).id))
        cls.project=cls.env['project.project'].sudo().with_context(**QUIET).create(dict(name='Synthetic organization project',company_id=cls.company.id,privacy_visibility='employees',user_id=False))
        cls.task=cls.env['project.task'].sudo().with_context(**QUIET).create(dict(name='Synthetic organization task',project_id=cls.project.id,user_ids=[]))
        cls.foreign_project=cls.env['project.project'].sudo().with_context(**QUIET).create(dict(name='Synthetic foreign project',company_id=cls.foreign_company.id,privacy_visibility='employees',user_id=False))
        cls.foreign_task=cls.env['project.task'].sudo().with_context(**QUIET).create(dict(name='Synthetic foreign task',project_id=cls.foreign_project.id,user_ids=[]))
        cls.private_project=cls.env['project.project'].sudo().with_context(**QUIET).create(dict(name='Synthetic private project',company_id=cls.company.id,privacy_visibility='invited_users',user_id=False))
        cls.private_partner=cls.env['res.partner'].sudo().with_context(**QUIET).create(dict(name='Synthetic private other contact',company_id=cls.company.id))
        cls.managed_partner=cls.env['res.partner'].sudo().with_context(**QUIET).create(dict(name='Synthetic managed private contact',company_id=cls.company.id,pe_managed=True,pe_org_id=1,pe_visibility='private',mcp_shared=True))

    def setUp(self):
        super().setUp()
        p=patch.object(type(self.env['muk_mcp.log']),'log',lambda *args,**kwargs:None)
        p.start();self.addCleanup(p.stop)

    def call(self,name,args=None,scope='write'):
        env=self.env(user=self.user)
        return env['muk_mcp.tool']._call_result(name,args or {},env,enforce_scope=scope)

    def data(self,name,args=None):
        result=self.call(name,args)
        self.assertFalse(result.get('isError'),result)
        return json.loads(result['content'][0]['text'])

    def test_exact_24_tools_and_40_models(self):
        self.assertEqual({v['name'] for v in self.env['muk_mcp.tool'].get_tools('mcp')},TOOLS)
        self.assertEqual({m['model'] for m in self.data('list_models',{'limit':100})},set(READ))

    def test_task_create_read_update_delete(self):
        row=self.data('create_records',{'model':'project.task','values':{'name':'Synthetic API task','project_id':self.project.id}})
        self.data('update_records',{'model':'project.task','ids':[row['id']],'values':{'name':'Synthetic updated task','priority':'1'}})
        record=self.data('read_records',{'model':'project.task','ids':[row['id']],'fields':['id','name','priority']})
        self.assertEqual(record[0]['name'],'Synthetic updated task')
        self.data('delete_records',{'model':'project.task','ids':[row['id']]})
        self.assertFalse(self.env['project.task'].sudo().browse(row['id']).exists())

    def test_cross_company_search_and_direct_read(self):
        rows=self.data('search_read',{'model':'project.task','fields':['id']})
        self.assertIn(self.task.id,[r['id'] for r in rows]);self.assertNotIn(self.foreign_task.id,[r['id'] for r in rows])
        self.assertTrue(self.call('read_records',{'model':'project.task','ids':self.foreign_task.ids,'fields':['name']})['isError'])
        self.assertNotIn(self.foreign_task.id,self.env['project.task'].with_user(self.user).search([]).ids)

    def test_private_projects_hidden(self):
        self.assertTrue(self.call('read_records',{'model':'project.project','ids':self.private_project.ids,'fields':['name']})['isError'])

    def test_private_and_managed_contacts_hidden(self):
        for partner in (self.private_partner,self.managed_partner):
            self.assertTrue(self.call('read_records',{'model':'res.partner','ids':partner.ids,'fields':['name','email']})['isError'])

    def test_business_contact_create_and_company_immutable(self):
        row=self.data('create_records',{'model':'res.partner','values':{'name':'Synthetic shared business contact','email':'fixture@example.invalid'}})
        partner=self.env['res.partner'].sudo().browse(row['id'])
        self.assertTrue(partner.mcp_shared);self.assertEqual(partner.company_id,self.company)
        self.assertTrue(self.call('update_records',{'model':'res.partner','ids':partner.ids,'values':{'company_id':self.foreign_company.id}})['isError'])
        self.assertTrue(self.call('update_records',{'model':'res.partner','ids':partner.ids,'values':{'pe_owner_id':self.user.id}})['isError'])
        self.assertTrue(self.call('update_records',{'model':'res.partner','ids':partner.ids,'values':{'type':'private'}})['isError'])

    def test_private_relation_is_rejected(self):
        self.assertTrue(self.call('create_records',{'model':'crm.lead','values':{'name':'Denied','partner_id':self.private_partner.id}})['isError'])

    def test_crm_create_update_and_archive(self):
        row=self.data('create_records',{'model':'crm.lead','values':{'name':'Synthetic CRM opportunity','type':'opportunity','expected_revenue':123}})
        self.data('update_records',{'model':'crm.lead','ids':[row['id']],'values':{'priority':'1'}})
        self.data('call_method',{'model':'crm.lead','ids':[row['id']],'method':'action_archive'})
        self.assertFalse(self.env['crm.lead'].sudo().browse(row['id']).active)

    def test_nested_relation_cannot_change_company_or_private_data(self):
        self.assertTrue(self.call('update_records',{'model':'project.task','ids':self.task.ids,'values':{'user_ids':[(6,0,[1])]}})['isError'])
        self.assertTrue(self.call('create_records',{'model':'sale.order','values':{'partner_id':self.private_partner.id,'order_line':[(0,0,{'name':'Denied','company_id':self.foreign_company.id})]}})['isError'])

    def test_technical_models_and_secret_fields_denied(self):
        for model in ('ir.config_parameter','ir.actions.server','muk_mcp.key','pe.bridge.config','res.users.apikeys'):
            self.assertTrue(self.call('search_read',{'model':model,'fields':['id']})['isError'])
        self.assertTrue(self.call('read_records',{'model':'res.users','ids':self.user.ids,'fields':['password']})['isError'])
        self.assertTrue(self.call('read_records',{'model':'res.users','ids':self.user.ids,'fields':['partner_id']})['isError'])

    def test_generic_methods_and_context_cannot_bypass(self):
        for args in (
            {'model':'project.task','ids':self.task.ids,'method':'write','args':'[{"name":"Denied"}]'},
            {'model':'project.task','ids':self.task.ids,'method':'action_archive','kwargs':{'context':{'sudo':True}}},
            {'model':'project.task','ids':self.task.ids,'method':'sudo'},
        ):self.assertTrue(self.call('call_method',args)['isError'])
        self.assertTrue(self.call('search_read',{'model':'project.task','fields':['id'],'context':{'allowed_company_ids':self.foreign_company.ids}})['isError'])

    def test_read_key_cannot_write(self):
        self.assertTrue(self.call('create_records',{'model':'project.task','values':{'name':'Denied','project_id':self.project.id}},scope='read')['isError'])

    def test_no_expiry_key_and_revoke(self):
        key,token=self.env['muk_mcp.key'].with_user(self.user)._generate('Continuous fixture','write',60)
        self.assertFalse(key.expires_at);self.assertEqual(self.env['muk_mcp.key'].authenticate(token),key)
        key.active=False;self.assertFalse(self.env['muk_mcp.key'].authenticate(token))
        with self.assertRaises(AccessError):key.active=True

    def test_temporary_key_expiry_is_enforced(self):
        key,token=self.env['muk_mcp.key'].with_user(self.user)._generate('Temporary fixture','write',60)
        key.expires_at=fields.Datetime.now()-timedelta(seconds=1)
        self.assertFalse(self.env['muk_mcp.key'].authenticate(token))

    def test_archive_revokes_and_reenable_does_not_restore(self):
        key,token=self.env['muk_mcp.key'].with_user(self.user)._generate('Archive fixture','write',60)
        self.user.active=False;self.user.active=True
        self.assertFalse(key.active);self.assertFalse(self.env['muk_mcp.key'].authenticate(token))

    def test_role_drift_disables_auth_and_monitor_revokes(self):
        key,token=self.env['muk_mcp.key'].with_user(self.user)._generate('Drift fixture','write',60)
        self.user.group_ids=[fields.Command.link(self.env.ref('base.group_system').id)]
        self.assertFalse(self.env['muk_mcp.key'].authenticate(token))
        self.cfg._monitor_security();self.assertFalse(key.active)

    def test_company_drift_disables_auth(self):
        key,token=self.env['muk_mcp.key'].with_user(self.user)._generate('Company fixture','write',60)
        self.user.company_ids=[fields.Command.link(self.foreign_company.id)]
        self.assertFalse(self.env['muk_mcp.key'].authenticate(token))

    def test_missing_policy_fails_closed(self):
        self.env['muk_mcp_access.model'].sudo().search([('model_name','=','project.task')]).active=False
        self.assertTrue(self.call('search_read',{'model':'project.task','fields':['id']})['isError'])

    def test_describe_and_grouping_are_field_bounded(self):
        meta=self.data('describe_model',{'model':'res.partner'})
        self.assertNotIn('pe_contact_key',meta)
        self.assertTrue(self.call('describe_model',{'model':'res.users','fields':['password']})['isError'])
        self.assertTrue(self.call('search_read',{'model':'project.task','domain':[['partner_id.email','ilike','x']],'fields':['id']})['isError'])
        self.assertTrue(self.call('read_group',{'model':'res.users','aggregates':['password:max']})['isError'])
        groups=self.data('read_group',{'model':'project.task','groupby':['state'],'aggregates':['__count']})
        self.assertTrue(groups)

    def test_global_reference_is_company_stamped(self):
        row=self.data('create_records',{'model':'crm.tag','values':{'name':'Synthetic scoped tag'}})
        self.assertEqual(self.env['crm.tag'].sudo().browse(row['id']).mcp_company_id,self.company)
        other=self.env['crm.tag'].sudo().create({'name':'Synthetic foreign tag','mcp_company_id':self.foreign_company.id})
        self.assertTrue(self.call('read_records',{'model':'crm.tag','ids':other.ids,'fields':['name']})['isError'])

    def test_foreign_attachment_hidden(self):
        attachment=self.env['ir.attachment'].sudo().create(dict(name='Synthetic foreign file',res_model='project.task',res_id=self.foreign_task.id,company_id=self.foreign_company.id,raw=b'private'))
        self.assertTrue(self.call('read_resource',{'uri':f'odoo://attachment/{attachment.id}'})['isError'])

    def test_file_transfer_bound_to_revocation(self):
        key,_=self.env['muk_mcp.key'].with_user(self.user)._generate('Transfer fixture','write',60)
        actor=self.env['muk_mcp.transfer'].with_user(self.user)
        with patch.object(org_models,'request',SimpleNamespace(_mcp_key=key)):
            transfer,url=actor._issue('upload',file_size=4)
        self.assertEqual(transfer.mcp_key_id,key)
        key.active=False
        self.assertTrue(transfer.used);self.assertFalse(actor._redeem(url.rsplit('/',1)[-1],'upload'))

    def test_calendar_of_other_user_denied(self):
        self.assertTrue(self.call('create_records',{'model':'calendar.event','values':{'name':'Denied','user_id':1,'start':'2026-11-01 12:00:00','stop':'2026-11-01 13:00:00'}})['isError'])

    def test_native_password_login_stays_denied(self):
        with self.assertRaises(AccessDenied):self.user.with_user(self.user)._check_credentials({'type':'password','password':'not-real'},{'interactive':True})

    def test_db_tool_override_stays_denied(self):
        self.env['muk_mcp.tool'].sudo().create({'name':'search_read','description':'Synthetic shadow','category':'read','code':'result = 42'})
        self.assertTrue(self.call('search_read',{'model':'project.task','fields':['id']})['isError'])
