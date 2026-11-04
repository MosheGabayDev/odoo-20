"""Personal keys require independently issued native consent on every request."""
import base64,hashlib,secrets,uuid
from cryptography.fernet import Fernet,InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from odoo import api,fields,models
from odoo.exceptions import AccessDenied,AccessError,ValidationError
from odoo.fields import Domain
from odoo.http import request
from odoo.addons.platform_bridge.models.bridge import request_scope,TOKEN
from odoo.addons.platform_mcp_organization.models import config,key_expiry
from odoo.addons.platform_mcp_organization.policy import READ,WRITE

PURPOSE='personal-business-mcp-v1'
FALSE=[('id','=',0)]


def codec(env):
    secret=env['pe.bridge.config']._config().sudo().client_secret
    if not secret or len(secret)<43:raise AccessDenied('Personal authority unavailable')
    derived=HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=b'odoo-personal-mcp-grant-v1').derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def expiry(self):
    cfg=config(self.env)
    return False if cfg and cfg.access_mode=='personal' and cfg.continuous else key_expiry(self)


class Config(models.Model):
    _inherit='pe.mcp.config'
    access_mode=fields.Selection(selection_add=[('personal','Personal Platform Engineer identity')],ondelete={'personal':'set default'})
    personal_user_id=fields.Many2one('res.users',readonly=True,groups=fields.NO_ACCESS)
    personal_owner_user_id=fields.Integer(readonly=True,groups=fields.NO_ACCESS)
    personal_transport_public_pem=fields.Text(readonly=True,groups=fields.NO_ACCESS)

    def _personal_target_ready(self):
        self.ensure_one();u=self.sudo().personal_user_id;b=self.env['pe.bridge.config']._config().sudo()
        return bool(self.org_id==1 and b.enabled and b.org_id==1 and b.company_id==self.company_id
            and u and u.active and self.personal_owner_user_id>0 and u.pe_user_id==self.personal_owner_user_id
            and u.pe_org_id==1 and u.pe_subject==f'pe:org:1:user:{self.personal_owner_user_id}'
            and u.company_id==self.company_id and u.company_ids==self.company_id
            and u.has_group('platform_mcp_personal.group_personal')
            and not any(u.has_group(g) for g in ('platform_mcp_guard.group_reader','base.group_system','base.group_erp_manager')))

    def _ready(self,*,enabled=True):
        if self.access_mode!='personal':return super()._ready(enabled=enabled)
        return bool((self.enabled or not enabled) and self._personal_target_ready() and self.user_id==self.sudo().personal_user_id)

    @api.constrains('access_mode','enabled','user_id','personal_user_id','personal_owner_user_id','org_id','company_id')
    def _check_personal(self):
        for cfg in self:
            if cfg.access_mode=='personal' and cfg.enabled and not cfg._ready():
                raise ValidationError('Personal MCP needs the verified selected SSO identity, business role and exact organization/company.')

    def _personal_identity(self,grant):
        response=self.env['pe.bridge.config']._call('/mcp/introspect',grant=grant)
        identity=self.env['pe.bridge.config']._identity(response)
        target=self.sudo().personal_user_id
        if (not self._personal_target_ready() or response.get('purpose')!=PURPOSE
                or response.get('active') is not True or response.get('continuous') is not True
                or 'expires_at' not in response or response.get('expires_at') is not None or identity['sub']!=target.pe_subject
                or identity['epoch']!=target.pe_native_epoch or identity['access_version']!=target.pe_access_version):
            raise AccessDenied('Personal consent changed')
        try:ident=str(uuid.UUID(response.get('consent_id')))
        except (TypeError,ValueError,AttributeError):raise AccessDenied('Personal consent unavailable') from None
        return dict(identity,odoo_uid=target.id),ident

    def _monitor_security(self):
        cfg=self._config()
        if not cfg or cfg.access_mode!='personal':return super()._monitor_security()
        keys=self.env['muk_mcp.key'].sudo().search([('active','=',True)])
        status='Personal access; continuous until revoked'
        if not cfg._ready():
            keys.write({'active':False});status='Invalid personal account or enrollment; keys revoked'
        else:
            for key in keys:
                try:key._personal_scope()
                except AccessDenied:status='Native consent denied or unavailable; requests remain blocked'
        cfg.write(dict(security_checked_at=fields.Datetime.now(),security_status=status))


class Users(models.Model):
    _inherit='res.users'

    def _mcp_restriction(self,model,write=False):
        cfg=config(self.env)
        if cfg and cfg.access_mode=='personal' and request and getattr(request,'_personal_mcp_key',None):
            return self._mcp_org_domain(model,write)
        return super()._mcp_restriction(model,write)

    def _mcp_org_domain(self,model,write=False):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='personal':return super()._mcp_org_domain(model,write)
        if not cfg._ready() or self.id!=cfg.user_id.id:return FALSE
        scope=self._pe_identity();company=cfg.company_id.id
        if model=='res.users':
            return [('active','=',True),('share','=',False),('pe_org_id','=',1),('pe_subject','!=',False)] if not write else FALSE
        if model=='res.partner':
            common=Domain([('pe_managed','=',True),('pe_org_id','=',1),('company_id','=',company)])
            ids=[str(v) for v in scope['write_contact_ids' if write else 'read_contact_ids']]
            available=Domain(['|',('pe_contact_key','=',False),('pe_contact_key','in',ids)])
            own=common & Domain('pe_owner_id','=',self.id) & available
            if write:return list(own)
            shared=common & Domain([('pe_visibility','=','org'),('pe_is_profile','=',False),('pe_contact_key','in',ids)])
            local=Domain([('mcp_shared','=',True),('company_id','=',company),('pe_managed','=',False),('type','!=','private'),('user_ids','=',False)])
            return list(own|shared|local|Domain('id','in',[self.partner_id.id,cfg.company_id.partner_id.id]))
        return super()._mcp_org_domain(model,write)

    def _pe_partner_domain(self,operation):
        result=super()._pe_partner_domain(operation);cfg=config(self.env)
        if (operation=='r' and cfg and cfg.access_mode=='personal' and cfg._ready()
                and self.id==cfg.user_id.id and request and getattr(request,'_personal_mcp_key',None)):
            self._pe_identity()
            return list(Domain(result)|Domain([('mcp_shared','=',True),('company_id','=',cfg.company_id.id),('pe_managed','=',False),('type','!=','private'),('user_ids','=',False)]))
        return result

    def write(self,values):
        affected=self.sudo().filtered(lambda u:u.has_group('platform_mcp_personal.group_personal')) if values.get('active') is False else self.browse()
        result=super().write(values)
        if affected:self.env['muk_mcp.key'].sudo().search([('user_id','in',affected.ids)]).write({'active':False})
        return result


class Keys(models.Model):
    _inherit='muk_mcp.key'
    expires_at=fields.Datetime(required=False,readonly=True,default=expiry)
    personal_grant_cipher=fields.Char(copy=False,readonly=True,prefetch=False,groups=fields.NO_ACCESS)
    personal_consent_id=fields.Char(copy=False,readonly=True,groups=fields.NO_ACCESS)
    personal_delivery_ciphertext=fields.Binary(copy=False,readonly=True,groups=fields.NO_ACCESS)

    def _personal_scope(self):
        self.ensure_one();cfg=config(self.env)
        if not cfg or cfg.access_mode!='personal' or not cfg._ready() or not self.active or self.user_id!=cfg.user_id:
            raise AccessDenied('Personal key unavailable')
        try:grant=codec(self.env).decrypt(self.sudo().personal_grant_cipher.encode()).decode()
        except (InvalidToken,AttributeError,ValueError,UnicodeError):raise AccessDenied('Personal authority unavailable') from None
        scope,ident=cfg._personal_identity(grant)
        if ident!=self.sudo().personal_consent_id:raise AccessDenied('Personal consent binding changed')
        return scope

    @api.constrains('user_id','scope','expires_at','personal_grant_cipher','personal_consent_id')
    def _check_guard(self):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='personal':return super()._check_guard()
        for key in self:
            if key.user_id!=cfg.user_id or key.scope!='write' or key.expires_at or not cfg.continuous:
                raise ValidationError('Personal business keys require continuous explicitly consented write access.')
            key._personal_scope()

    def _authenticate_domain(self):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='personal':return super()._authenticate_domain()
        if not cfg._ready():return Domain('id','=',0)
        return Domain([('user_id','=',cfg.user_id.id),('user_id.active','=',True),('scope','=','write'),('expires_at','=',False)])

    @api.model
    def authenticate(self,token):
        key=super().authenticate(token);cfg=config(self.env)
        if not cfg or cfg.access_mode!='personal' or not key:return key
        if not request:return self.browse()
        try:scope=key._personal_scope()
        except AccessDenied:return self.browse()
        request._pe_bridge_scope=scope;request._personal_mcp_key=key
        return key

    @api.model
    def _generate(self,name,scope,rate_limit):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='personal':return super()._generate(name,scope,rate_limit)
        consent=getattr(request,'_personal_mcp_consent',None) if request else None
        if not consent or self.env.uid!=cfg.user_id.id or scope!='write':raise AccessDenied('Use the personal native consent flow')
        native,ident=cfg._personal_identity(consent['grant'])
        if native['sub']!=self.env.user.sudo().pe_subject:raise AccessDenied('Wrong personal identity')
        raw=secrets.token_urlsafe(32)
        key=self.sudo().create(dict(name=name,user_id=self.env.uid,key_hash=self._hash_key(raw),key_prefix=raw[:8],scope='write',rate_limit=rate_limit,
            personal_grant_cipher=codec(self.env).encrypt(consent['grant'].encode()).decode(),personal_consent_id=ident))
        return key,raw

    def write(self,values):
        revoke=self.sudo().filtered(lambda k:k.active and k.personal_grant_cipher) if values.get('active') is False else self.browse()
        if {'personal_grant_cipher','personal_consent_id','personal_delivery_ciphertext'} & set(values):
            if set(values)!={'personal_delivery_ciphertext'} or any(k.sudo().personal_delivery_ciphertext for k in self):
                raise AccessError('Personal authority and delivery are immutable; reconnect with native consent')
        result=super().write(values)
        for key in revoke:
            try:
                grant=codec(self.env).decrypt(key.personal_grant_cipher.encode()).decode()
                self.env['pe.bridge.config']._call('/mcp/revoke',grant=grant)
            except (AccessDenied,InvalidToken,AttributeError,ValueError,UnicodeError):
                # Local denial is immediate and irreversible even when the
                # native service is unavailable. Its UI can revoke consent too.
                pass
        return result


class IrHttp(models.AbstractModel):
    _inherit='ir.http'

    @classmethod
    def _pe_authenticate_scope(cls,env,session):
        key=getattr(request,'_personal_mcp_key',None) if request else None
        if key:
            if key.user_id.id!=env.uid:raise AccessDenied('Wrong personal request identity')
            return key._personal_scope()
        return super()._pe_authenticate_scope(env,session)


class Tool(models.Model):
    _inherit='muk_mcp.tool'

    def _execute(self,name,arguments,env,enforce_scope):
        cfg=config(env)
        if not cfg or cfg.access_mode!='personal':return super()._execute(name,arguments,env,enforce_scope)
        key=getattr(request,'_personal_mcp_key',None) if request else None
        if not key or key.user_id.id!=env.uid:raise AccessError('Fresh personal native authority required')
        scope=key._personal_scope();request._pe_bridge_scope=scope
        result=super()._execute(name,arguments,env,enforce_scope)
        # Still inside MuK's operation savepoint: a changed/denied native scope
        # withholds the result and rolls back this tool's Odoo DB writes.
        if key._personal_scope()!=scope:raise AccessError('Personal authority changed during the operation; retry after review')
        return result


class Transfers(models.Model):
    _inherit='muk_mcp.transfer'

    @api.model
    def _redeem(self,token,operation):
        cfg=config(self.env)
        if cfg and cfg.access_mode=='personal':
            candidate=self.sudo().search([('token_hash','=',self.env['muk_mcp.key']._hash_key(token)),('operation','=',operation)],limit=1)
            key=candidate.mcp_key_id
            try:scope=key._personal_scope()
            except (AccessDenied,ValueError):return self.browse()
            if not request:return self.browse()
            request.update_env(user=key.user_id.id);request._pe_bridge_scope=scope;request._personal_mcp_key=key;request._mcp_key=key
        return super()._redeem(token,operation)
