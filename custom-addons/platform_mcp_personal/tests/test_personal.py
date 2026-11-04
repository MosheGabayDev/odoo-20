"""Synthetic linked identities and native authority; never an owner grant."""
import base64,uuid
from types import SimpleNamespace
from unittest.mock import patch
from odoo import api,fields
from odoo.exceptions import AccessDenied,AccessError,ValidationError
from odoo.tests import TransactionCase,tagged
from odoo.addons.platform_bridge.models import bridge,access
from odoo.addons.platform_mcp_organization.policy import READ,WRITE
from odoo.addons.platform_mcp_organization.tools import QUIET
from .. import models as personal


@tagged('post_install','-at_install')
class TestPersonalMCP(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cfg=cls.env.ref('platform_mcp_guard.main').sudo();cls.cfg.enabled=False
        b=cls.env.ref('platform_bridge.main').sudo();b.write(dict(enabled=True,org_id=1,company_id=cls.env.company.id,client_secret='s'*64))
        cls.one=cls.env['res.users'].sudo().with_context(**QUIET).create(dict(name='Synthetic personal MCP',login='personal-mcp-fixture',
            company_id=cls.env.company.id,company_ids=[fields.Command.set(cls.env.company.ids)],
            group_ids=[fields.Command.set((cls.env.ref('platform_mcp_personal.group_personal')|cls.env.ref('base.group_partner_manager')).ids)],
            pe_subject='pe:org:1:user:9101',pe_org_id=1,pe_user_id=9101,pe_access_version=1,pe_native_epoch=4))
        cls.two=cls.env['res.users'].sudo().with_context(**QUIET).create(dict(name='Synthetic other owner',login='personal-mcp-other',
            company_id=cls.env.company.id,company_ids=[fields.Command.set(cls.env.company.ids)],group_ids=[fields.Command.set(cls.env.ref('base.group_user').ids)],
            pe_subject='pe:org:1:user:9102',pe_org_id=1,pe_user_id=9102,pe_access_version=1,pe_native_epoch=4))
        cls.cfg.write(dict(access_mode='personal',user_id=cls.one.id,personal_user_id=cls.one.id,personal_owner_user_id=9101,
            company_id=cls.env.company.id,org_id=1,continuous=True,enabled=True))
        cls.project=cls.env['project.project'].sudo().with_context(**QUIET).create(dict(name='Synthetic personal project',company_id=cls.env.company.id,privacy_visibility='employees',user_id=cls.one.id))
        cls.task=cls.env['project.task'].sudo().with_context(**QUIET).create(dict(name='Synthetic personal task',project_id=cls.project.id,user_ids=[fields.Command.set(cls.one.ids)]))
        def contact(name,key,owner,visibility='private'):
            return cls.env['res.partner'].sudo().with_context(**QUIET).create(dict(name=name,company_id=cls.env.company.id,
                pe_managed=True,pe_org_id=1,pe_contact_key=key,pe_owner_id=owner.id,pe_visibility=visibility))
        cls.own=contact('Synthetic own private contact','3101',cls.one)
        cls.other=contact('Synthetic other private contact','3102',cls.two)
        cls.shared=contact('Synthetic organization contact','3103',cls.two,'org')
        cls.foreign_company=cls.env['res.company'].sudo().create({'name':'Synthetic personal foreign company'})
        cls.foreign_project=cls.env['project.project'].sudo().with_context(**QUIET).create(dict(name='Synthetic foreign personal project',company_id=cls.foreign_company.id,privacy_visibility='employees',user_id=False))
        cls.foreign_task=cls.env['project.task'].sudo().with_context(**QUIET).create(dict(name='Synthetic foreign task',project_id=cls.foreign_project.id,user_ids=[]))
        for model in READ:
            row=cls.env['muk_mcp_access.model'].sudo().with_context(active_test=False).search([('model_name','=',model)],limit=1)
            values=dict(active=True,allow_read=True,allow_write=model in WRITE,domain=f"user._mcp_org_domain('{model}')")
            if row:row.write(values)
            else:cls.env['muk_mcp_access.model'].sudo().create(dict(values,model_id=cls.env['ir.model']._get(model).id))

    def setUp(self):
        super().setUp()
        self.native=dict(org=1,user=9101,epoch=4,access_version=1,sub=self.one.pe_subject,
            first_name='Synthetic',last_name='Personal',email='personal@example.invalid',read_contact_ids=[3101,3103],write_contact_ids=[3101])
        self.consent_id=str(uuid.uuid4());self.revoked=False
        self.req=SimpleNamespace(_pe_bridge_scope=dict(self.native,odoo_uid=self.one.id))
        def remote(model,endpoint,**kwargs):
            if self.revoked:raise AccessDenied('Synthetic revoked consent')
            return dict(active=True,issuer=bridge.ORIGIN,audience=bridge.CLIENT,identity=dict(self.native),
                purpose=personal.PURPOSE,consent_id=self.consent_id,continuous=True,expires_at=None)
        patchers=[patch.object(personal,'request',self.req),
            patch.object(bridge,'request_scope',lambda:self.req._pe_bridge_scope),
            patch.object(access,'request_scope',lambda:self.req._pe_bridge_scope),
            patch.object(type(self.env['pe.bridge.config']),'_call',remote),
            patch.object(type(self.env['muk_mcp.log']),'log',lambda *a,**kw:None)]
        for p in patchers:p.start();self.addCleanup(p.stop)
        self.key=self.env['muk_mcp.key'].sudo().create(dict(name='Synthetic personal key',user_id=self.one.id,key_hash=self.env['muk_mcp.key']._hash_key('k'*43),
            key_prefix='fixture',scope='write',rate_limit=240,personal_grant_cipher=personal.codec(self.env).encrypt(('g'*43).encode()).decode(),personal_consent_id=self.consent_id))
        self.req._personal_mcp_key=self.key

    def call(self,name,args=None):
        env=self.env(user=self.one)
        return env['muk_mcp.tool']._call_result(name,args or {},env,enforce_scope='write')

    def data(self,name,args=None):
        import json
        result=self.call(name,args);self.assertFalse(result.get('isError'),result)
        return json.loads(result['content'][0]['text'])

    def test_personal_role_keeps_ordinary_sso_and_never_system_admin(self):
        self.assertFalse(self.one.has_group('platform_mcp_guard.group_reader'))
        self.assertFalse(self.one.has_group('base.group_system'));self.assertFalse(self.one.has_group('base.group_erp_manager'))
        result=self.one.with_user(self.one)._check_credentials(dict(type='pe_grant',login=self.one.login,grant='g'*43),{'interactive':True})
        self.assertEqual(result['uid'],self.one.id)

    def test_personal_identity_and_exact_business_surface(self):
        who=self.data('whoami');self.assertTrue(who['personal']);self.assertEqual(who['native_subject'],self.one.pe_subject)
        self.assertEqual(len(self.env['muk_mcp.tool'].get_tools('mcp')),24)
        self.assertEqual(len(self.data('list_models',{'limit':100})),40)

    def test_own_private_and_org_contact_visible_other_owner_private_denied(self):
        ids=[row['id'] for row in self.data('search_read',{'model':'res.partner','fields':['id','name']})]
        self.assertIn(self.own.id,ids);self.assertIn(self.shared.id,ids);self.assertNotIn(self.other.id,ids)
        self.assertTrue(self.call('read_records',{'model':'res.partner','ids':self.other.ids,'fields':['id','email']})['isError'])

    def test_private_relation_and_foreign_task_denied(self):
        self.assertTrue(self.call('create_records',{'model':'crm.lead','values':{'name':'Synthetic denied','partner_id':self.other.id}})['isError'])
        self.assertTrue(self.call('read_records',{'model':'project.task','ids':self.foreign_task.ids,'fields':['id','name']})['isError'])

    def test_business_crud_works_for_selected_personal_identity(self):
        row=self.data('create_records',{'model':'project.task','values':{'name':'Synthetic personal CRUD','project_id':self.project.id}})
        self.data('update_records',{'model':'project.task','ids':[row['id']],'values':{'priority':'1'}})
        self.assertEqual(self.data('read_records',{'model':'project.task','ids':[row['id']],'fields':['priority']})[0]['priority'],'1')
        self.data('delete_records',{'model':'project.task','ids':[row['id']]})
        self.assertFalse(self.env['project.task'].sudo().browse(row['id']).exists())

    def test_revoked_native_consent_rejects_still_active_key(self):
        self.assertEqual(self.env['muk_mcp.key'].authenticate('k'*43),self.key)
        self.revoked=True
        self.assertFalse(self.env['muk_mcp.key'].authenticate('k'*43))
        self.assertTrue(self.call('search_read',{'model':'project.task','fields':['id']})['isError'])

    def test_native_epoch_and_owner_mismatch_reject_key(self):
        for key,value in [('epoch',5),('access_version',2),('sub','pe:org:1:user:9102')]:
            old=self.native[key];self.native[key]=value
            self.assertFalse(self.env['muk_mcp.key'].authenticate('k'*43));self.native[key]=old

    def test_missing_request_scope_cannot_be_forged_with_context(self):
        del self.req._personal_mcp_key
        self.assertTrue(self.call('search_read',{'model':'res.partner','fields':['id']})['isError'])
        self.req._personal_mcp_key=self.key;self.req._pe_bridge_scope=None
        with self.assertRaises(AccessDenied):self.one.with_user(self.one).with_context(pe_read_contact_ids=[3102])._pe_identity()

    def test_native_scope_change_withholds_result_and_rolls_back_write(self):
        original=self.task.name;scope=dict(self.req._pe_bridge_scope);changed=dict(scope,read_contact_ids=[])
        with patch.object(type(self.key),'_personal_scope',side_effect=[scope,changed]):
            result=self.call('update_records',{'model':'project.task','ids':self.task.ids,'values':{'name':'Synthetic must roll back'}})
        self.assertTrue(result['isError']);self.task.invalidate_recordset();self.assertEqual(self.task.name,original)

    def test_archive_reenable_does_not_restore_local_key(self):
        self.one.sudo().write({'active':False});self.one.sudo().write({'active':True})
        self.assertFalse(self.key.active);self.assertFalse(self.env['muk_mcp.key'].authenticate('k'*43))

    def test_arbitrary_context_model_method_and_private_fields_denied(self):
        for name,args in [('search_read',{'model':'ir.config_parameter','fields':['id']}),
            ('read_records',{'model':'res.users','ids':self.one.ids,'fields':['password']}),
            ('search_read',{'model':'project.task','fields':['id'],'context':{'sudo':True}}),
            ('call_method',{'model':'project.task','ids':self.task.ids,'method':'write','args':'[{"name":"Denied"}]'})]:
            self.assertTrue(self.call(name,args)['isError'])

    def test_generation_requires_actual_native_consent_callback(self):
        with self.assertRaises(AccessDenied):self.env['muk_mcp.key'].with_user(self.one)._generate('Denied','write',120)
        self.req._personal_mcp_consent={'grant':'g'*43}
        key,raw=self.env['muk_mcp.key'].with_user(self.one)._generate('Synthetic callback','write',120)
        self.assertFalse(key.expires_at);self.assertEqual(len(raw),43)
        self.assertNotIn('g'*43,key.sudo().personal_grant_cipher)

    def test_clone_neutralization_clears_inline_personal_delivery_and_authority(self):
        from odoo.tools import file_open
        self.assertFalse(self.key._fields['personal_delivery_ciphertext'].attachment)
        self.key.sudo().write({'personal_delivery_ciphertext':b'Y2lwaGVydGV4dA=='})
        # CLI neutralization sees committed storage, not pending ORM writes.
        self.env.flush_all()
        with file_open('platform_mcp_personal/data/neutralize.sql') as source:
            self.env.cr.execute(source.read())
        self.key.invalidate_recordset()
        self.assertFalse(self.key.active)
        self.assertFalse(self.key.sudo().personal_grant_cipher)
        self.assertFalse(self.key.sudo().personal_delivery_ciphertext)

    def test_grant_and_delivery_are_not_exposed_to_personal_user(self):
        for field in ['personal_grant_cipher','personal_consent_id','personal_delivery_ciphertext']:
            with self.assertRaises(AccessError):self.key.with_user(self.one).read([field])
        with self.assertRaises(AccessError):self.key.sudo().write({'personal_consent_id':str(uuid.uuid4())})

    def test_other_private_profile_is_not_a_contact_exception(self):
        self.assertTrue(self.call('read_records',{'model':'res.partner','ids':self.two.partner_id.ids,'fields':['id','name','email']})['isError'])

    def test_transfer_redeem_rechecks_native_revocation(self):
        attachment=self.env['ir.attachment'].with_user(self.one).create(dict(name='Synthetic personal file',raw=b'synthetic',res_model='project.task',res_id=self.task.id))
        # Issue needs the authenticated key, not a caller-supplied ORM context.
        from odoo.addons.platform_mcp_organization import models as org_models
        self.req._mcp_key=self.key
        with patch.object(org_models,'request',self.req):
            transfer,url=self.env['muk_mcp.transfer'].with_user(self.one)._issue('download',attachment_id=attachment.id)
        self.revoked=True
        self.assertFalse(self.env['muk_mcp.transfer']._redeem(url.rsplit('/',1)[-1],'download'))
