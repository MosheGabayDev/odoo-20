from unittest.mock import patch

from odoo import api, fields
from odoo.exceptions import AccessDenied, AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.addons.platform_bridge.models import bridge, access


@tagged('post_install','-at_install')
class TestBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config=cls.env.ref('platform_bridge.main')
        cls.config.write({'org_id':11})
        groups=[cls.env.ref('base.group_user').id,cls.env.ref('base.group_partner_manager').id]
        cls.users=[]
        for uid in (901,902):
            cls.users.append(cls.env['res.users'].sudo().create(dict(
                name=f'Synthetic PE {uid}',login=f'pe-fixture-{uid}',
                email=f'pe-{uid}@example.invalid',
                company_id=cls.env.company.id,company_ids=[fields.Command.set(cls.env.company.ids)],
                group_ids=[fields.Command.set(groups)],pe_org_id=11,pe_user_id=uid,
                pe_subject=f'pe:org:11:user:{uid}',pe_access_version=1)))
        cls.one,cls.two=cls.users
        def contact(name,key,owner,org=11):
            return cls.env['res.partner'].sudo().create(dict(name=name,pe_managed=True,
                pe_org_id=org,pe_contact_key=key,pe_owner_id=owner.id,company_id=cls.env.company.id))
        cls.private_one=contact('Synthetic private one','1001',cls.one)
        cls.private_two=contact('Synthetic private two','1002',cls.two)
        cls.org_contact=contact('Synthetic organization','1003',cls.one)
        cls.foreign=contact('Synthetic foreign org','2001',cls.one,22)
        cls.legacy=cls.env['res.partner'].sudo().create({'name':'Synthetic unclassified legacy'})

    def setUp(self):
        super().setUp()
        self.scope=dict(odoo_uid=self.one.id,org=11,user=901,epoch=4,access_version=1,
            sub=self.one.pe_subject,first_name='Synthetic',last_name='One',
            email='synthetic@example.invalid',read_contact_ids=[1001,1003])
        self.patchers=[patch.object(bridge,'request_scope',lambda:self.scope),
                       patch.object(access,'request_scope',lambda:self.scope)]
        for p in self.patchers:p.start();self.addCleanup(p.stop)

    def as_one(self,nonce='normal'):
        return self.env['res.partner'].with_user(self.one).with_context(bridge_test_nonce=nonce)

    def test_private_and_org_visibility_and_legacy_hold(self):
        ids=self.as_one().search([]).ids
        self.assertIn(self.private_one.id,ids)
        self.assertIn(self.org_contact.id,ids)
        self.assertNotIn(self.private_two.id,ids)
        self.assertNotIn(self.foreign.id,ids)
        self.assertNotIn(self.legacy.id,ids)
        with self.assertRaises(AccessError):
            self.as_one().browse(self.private_two.id).read(['name'])

    def test_fresh_manifest_removal_blocks_old_local_copy(self):
        self.assertIn(self.private_one.id,self.as_one('before').search([]).ids)
        self.scope['read_contact_ids']=[]
        self.assertNotIn(self.private_one.id,self.as_one('after').search([]).ids)
        with self.assertRaises(AccessError):
            self.as_one('after-write').browse(self.private_one.id).write({'phone':'+15551234567'})

    def test_owner_only_writes_even_for_visible_org_contact(self):
        self.scope.update(odoo_uid=self.two.id,user=902,sub=self.two.pe_subject,
                          read_contact_ids=[1002,1003])
        model=self.env['res.partner'].with_user(self.two)
        self.assertIn(self.org_contact.id,model.search([]).ids)
        with self.assertRaises(AccessError):model.browse(self.org_contact.id).write({'name':'No authority'})
        model.browse(self.private_two.id).write({'phone':'+15551234567'})

    def test_new_contact_is_always_owned_private_and_bound_to_org(self):
        contact=self.as_one().create({'name':'Synthetic new contact'})
        self.assertEqual(contact.sudo().pe_org_id,11)
        self.assertEqual(contact.sudo().pe_owner_id,self.one)
        self.assertEqual(contact.sudo().pe_visibility,'private')
        self.assertFalse(contact.sudo().pe_contact_key)
        with self.assertRaises(AccessError):
            self.as_one().create({'name':'Forged','pe_org_id':22})

    def test_binding_forgery_archive_and_delete_deny(self):
        contact=self.as_one().browse(self.private_one.id)
        for values in [{'pe_owner_id':self.two.id},{'pe_contact_key':'1002'},
                       {'active':False},{'pe_visibility':'org'}]:
            with self.assertRaises(AccessError):contact.write(values)
        with self.assertRaises(AccessError):contact.unlink()

    def test_no_scope_and_rpc_context_forgery_fail_closed(self):
        self.scope=None
        with self.assertRaises(AccessDenied):
            self.as_one('none').with_context(pe_read_contact_ids=[1001,1002,1003]).search([])

    def test_remote_tokens_and_configuration_secret_are_not_readable(self):
        with self.assertRaises(AccessError):
            self.one.with_user(self.one).read(['pe_contacts_grant'])
        with self.assertRaises(AccessError):
            self.config.with_user(self.one).read(['client_secret'])
        with self.assertRaises(AccessError):
            self.one.with_user(self.one).write({'pe_access_version':99})

    def test_password_cannot_bypass_native_checkbox(self):
        with self.assertRaises(AccessDenied):
            self.one.with_user(self.one)._check_credentials(
                {'type':'password','login':self.one.login,'password':'irrelevant'}, {'interactive':True})

    def test_identity_issuer_audience_org_and_numeric_types(self):
        identity={k:v for k,v in self.scope.items() if k!='odoo_uid'}
        valid=dict(issuer=bridge.ORIGIN,audience=bridge.CLIENT,identity=identity)
        self.assertEqual(self.config._identity(valid)['user'],901)
        for bad in [dict(valid,issuer='https://evil.invalid'),dict(valid,audience='other'),
                    dict(valid,identity=dict(identity,org=22)),dict(valid,identity=dict(identity,user=True))]:
            with self.assertRaises(AccessDenied):self.config._identity(bad)

    def test_no_email_based_existing_account_takeover_or_admin_grant(self):
        identity={k:v for k,v in self.scope.items() if k!='odoo_uid'}
        identity.update(user=903,sub='pe:org:11:user:903',email=self.env.ref('base.user_admin').email or 'admin@example.invalid')
        user=self.config._provision(identity,'g'*43)
        self.assertNotEqual(user,self.env.ref('base.user_admin'))
        self.assertFalse(user.has_group('base.group_system'))
        self.assertFalse(user.has_group('base.group_erp_manager'))

    def test_organization_cannot_be_rebound_over_existing_accounts(self):
        with self.assertRaises(ValidationError):self.config.write({'org_id':22})

    def test_local_admin_still_sees_existing_legacy_contacts(self):
        self.assertIn(self.legacy.id,self.env['res.partner'].with_user(self.env.ref('base.user_admin')).search([]).ids)
