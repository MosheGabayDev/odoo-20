"""One explicitly enrolled Platform organization per Odoo database."""
import hashlib
import json
import re

import requests

from odoo import api, fields, models
from odoo.exceptions import AccessDenied, AccessError, ValidationError
from odoo.http import request
from odoo.fields import Domain

CLIENT = 'odoo20-test'
ORIGIN = 'https://test.ai-data-platform.com/integrations/odoo'
CALLBACK = 'https://odoo.test.ai-data-platform.com/platform/signin'
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')


def request_scope():
    """Only an authenticated request can supply this server-created scope."""
    return getattr(request, '_pe_bridge_scope', None) if request else None


class Config(models.Model):
    _name = 'pe.bridge.config'
    _description = 'Platform Engineer bridge configuration'

    name = fields.Char(default='Platform Engineer', required=True)
    enabled = fields.Boolean(default=False)
    writes_enabled = fields.Boolean(default=False)
    org_id = fields.Integer(string='Platform organization ID')
    company_id = fields.Many2one('res.company', required=True, default=lambda self:self.env.company)
    client_secret = fields.Char(copy=False, groups=fields.NO_ACCESS, prefetch=False)

    def write(self, vals):
        if 'org_id' in vals:
            for config in self:
                if (vals['org_id']!=config.org_id and self.env['res.users'].sudo()
                        .with_context(active_test=False).search_count([('pe_subject','!=',False)])):
                    raise ValidationError('Existing mapped identities require a separate tenant database.')
        return super().write(vals)

    @api.constrains('enabled','org_id','company_id')
    def _check_enrollment(self):
        for config in self:
            if config.enabled and config.org_id <= 0:
                raise ValidationError('Choose the Platform organization before enabling the bridge.')

    @api.model
    def _config(self):
        records=self.sudo().search([],limit=2)
        if len(records)!=1:
            raise AccessDenied('Bridge configuration unavailable')
        return records

    @api.model
    def _call(self, endpoint, *, grant=None, data=None, params=None, method='POST'):
        if endpoint not in ('/token','/introspect','/contacts','/contacts/apply'):
            raise AccessDenied('Unsupported bridge operation')
        config=self._config()
        if not config.enabled or config.org_id<=0 or not config.client_secret:
            raise AccessDenied('Platform sign-in is unavailable')
        if grant is not None and (not isinstance(grant,str) or not TOKEN.fullmatch(grant)):
            raise AccessDenied('Platform authorization unavailable')
        headers={'X-Odoo-Grant':grant} if grant else {}
        try:
            with requests.Session() as session:
                session.trust_env=False
                kwargs=dict(auth=(CLIENT,config.client_secret),headers=headers,
                            timeout=(3,8),allow_redirects=False,params=params)
                if endpoint=='/token':kwargs['data']=data
                elif data is not None:kwargs['json']=data
                response=session.request(method,ORIGIN+endpoint,**kwargs)
                if response.status_code!=200 or len(response.content)>2*1024*1024:
                    raise AccessDenied('Platform authorization unavailable')
                result=response.json()
        except (requests.RequestException,ValueError):
            raise AccessDenied('Platform authorization unavailable') from None
        if not isinstance(result,dict):
            raise AccessDenied('Platform authorization unavailable')
        return result

    @api.model
    def _identity(self, response):
        config=self._config()
        identity=response.get('identity')
        if response.get('issuer')!=ORIGIN or response.get('audience')!=CLIENT or not isinstance(identity,dict):
            raise AccessDenied('Invalid Platform identity')
        for key in ('org','user','epoch','access_version'):
            value=identity.get(key)
            if type(value) is not int or value < (1 if key in ('org','user') else 0) or value>2147483647:
                raise AccessDenied('Invalid Platform identity')
        expected=f"pe:org:{identity['org']}:user:{identity['user']}"
        if identity.get('sub')!=expected or identity['org']!=config.org_id:
            raise AccessDenied('Wrong Platform organization')
        ids=identity.get('read_contact_ids')
        if not isinstance(ids,list) or len(ids)>100000 or any(type(v)is not int or v<=0 for v in ids):
            raise AccessDenied('Contact authorization unavailable')
        if any(not isinstance(identity.get(k),str) or len(identity[k])>255 for k in ('first_name','last_name','email')):
            raise AccessDenied('Invalid Platform profile')
        return identity

    @api.model
    def _provision(self, identity, contacts_grant):
        config=self._config()
        if not isinstance(contacts_grant,str) or not TOKEN.fullmatch(contacts_grant):
            raise AccessDenied('Contact authorization unavailable')
        users=self.env['res.users'].sudo().with_context(active_test=False)
        user=users.search([('pe_subject','=',identity['sub'])],limit=1)
        if user:
            if not user.active or user.pe_org_id!=config.org_id or user.company_ids!=config.company_id:
                raise AccessDenied('Mapped Odoo account is unavailable')
            # Existing groups are not expanded or replaced by upstream admin roles.
            user.write({'pe_contacts_grant':contacts_grant,'pe_access_version':identity['access_version']})
            return user
        login=f"pe-org{identity['org']}-user{identity['user']}"
        if users.search_count([('login','=',login)]):
            # Never attach by email, login resemblance or a conflicting local account.
            raise AccessDenied('Explicit account-link review required')
        user=users.create(dict(name=(identity['first_name']+' '+identity['last_name']).strip(),
            login=login,email=identity['email'],company_id=config.company_id.id,
            company_ids=[fields.Command.set(config.company_id.ids)],
            group_ids=[fields.Command.set([self.env.ref('base.group_user').id,
                                         self.env.ref('base.group_partner_manager').id])],
            pe_subject=identity['sub'],pe_org_id=identity['org'],pe_user_id=identity['user'],
            pe_access_version=identity['access_version'],pe_contacts_grant=contacts_grant))
        return user


class Users(models.Model):
    _inherit = 'res.users'

    pe_subject = fields.Char(copy=False,index=True,readonly=True)
    pe_org_id = fields.Integer(copy=False,readonly=True)
    pe_user_id = fields.Integer(copy=False,readonly=True)
    pe_access_version = fields.Integer(copy=False,readonly=True)
    pe_contacts_grant = fields.Char(copy=False,prefetch=False,groups=fields.NO_ACCESS)
    _pe_unique_subject = models.Constraint('unique(pe_subject)','Platform identity must be unique')

    def write(self, vals):
        if not self.env.su and any(k.startswith('pe_') for k in vals):
            raise AccessError('Platform identity is managed by the bridge')
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(any(k.startswith('pe_') for k in vals) for vals in vals_list):
            raise AccessError('Platform identity is managed by the bridge')
        return super().create(vals_list)

    def _check_credentials(self, credential, env):
        if not self.sudo().pe_subject:
            return super()._check_credentials(credential,env)
        if credential.get('type')!='pe_grant' or not self.active:
            raise AccessDenied('Use Platform Engineer sign-in')
        bridge=self.env['pe.bridge.config']
        identity=bridge._identity(bridge._call('/introspect',grant=credential.get('grant')))
        if (identity['sub']!=self.sudo().pe_subject
                or identity['access_version']!=self.sudo().pe_access_version):
            raise AccessDenied('Platform account changed')
        return dict(uid=self.id,auth_method='platform_engineer',mfa='default')

    def _get_session_token_fields(self):
        return super()._get_session_token_fields() | {'pe_subject','pe_access_version'}

    def _pe_identity(self):
        self.ensure_one()
        scope=request_scope()
        if not scope or scope.get('odoo_uid')!=self.id or scope.get('sub')!=self.sudo().pe_subject:
            raise AccessDenied('Fresh Platform authorization required')
        return scope

    def _pe_partner_domain(self, operation):
        self.ensure_one()
        if not self.sudo().pe_subject:
            return [] if self.has_group('base.group_system') else [('pe_managed','=',False)]
        scope=self._pe_identity()
        if operation=='d':return [(1,'=',0)]
        common=[('pe_managed','=',True),('pe_org_id','=',scope['org'])]
        if operation=='r':
            ids=[str(v) for v in scope['read_contact_ids']]
            mapped=common+[('pe_contact_key','in',ids)]
            pending=common+[('pe_contact_key','=',False),('pe_owner_id','=',self.id)]
            own=[('id','in',[self.partner_id.id,self.company_id.partner_id.id])]
            staff=[('user_ids.pe_org_id','=',scope['org']),('user_ids.pe_subject','!=',False)]
            return list(Domain.OR([Domain(mapped),Domain(pending),Domain(own),Domain(staff)]))
        allowed=list(Domain.OR([Domain([('pe_contact_key','=',False)]),
            Domain([('pe_contact_key','in',[str(v) for v in scope['read_contact_ids']])])]))
        return common+[('pe_owner_id','=',self.id)]+allowed

    def _pe_user_domain(self):
        self.ensure_one()
        if not self.sudo().pe_subject:
            return [] if self.has_group('base.group_system') else [('pe_subject','=',False)]
        scope=self._pe_identity()
        return [('pe_org_id','=',scope['org']),('pe_subject','!=',False)]


class Partner(models.Model):
    _inherit = 'res.partner'

    pe_managed = fields.Boolean(copy=False,index=True,readonly=True)
    pe_org_id = fields.Integer(copy=False,index=True,readonly=True)
    pe_contact_key = fields.Char(copy=False,index=True,readonly=True)
    pe_owner_id = fields.Many2one('res.users',copy=False,readonly=True)
    pe_visibility = fields.Selection([('private','Private'),('org','Organization')],default='private',copy=False,readonly=True)
    pe_first_name = fields.Char(copy=False)
    pe_last_name = fields.Char(copy=False)
    _pe_unique_contact = models.Constraint('unique(pe_org_id,pe_contact_key)','Platform contact must be unique')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and self.env.user.sudo().pe_subject:
            scope=self.env.user._pe_identity()
            vals_list=[dict(vals) for vals in vals_list]
            for vals in vals_list:
                if any(k.startswith('pe_') for k in vals):
                    raise AccessError('Platform contact binding is managed by the bridge')
                vals.update(pe_managed=True,pe_org_id=scope['org'],pe_owner_id=self.env.uid,
                    pe_visibility='private',pe_first_name=vals.get('name') or '',pe_last_name='',
                    company_id=self.env.user.company_id.id)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and self.env.user.sudo().pe_subject:
            if any(k.startswith('pe_') for k in vals):
                raise AccessError('Platform contact binding is managed by the bridge')
            if 'company_id' in vals and vals['company_id']!=self.env.user.company_id.id:
                raise AccessError('Platform organization is fixed')
            if 'active' in vals:
                raise AccessError('Archive or deletion requires a reviewed synchronization action')
        return super().write(vals)
