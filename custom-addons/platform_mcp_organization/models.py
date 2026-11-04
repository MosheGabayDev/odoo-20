"""Organization service authority; existing PE-linked SSO remains untouched."""
import json
from datetime import timedelta
from odoo import api,fields,models
from odoo.exceptions import AccessError,ValidationError
from odoo.fields import Domain
from odoo.http import request
from .policy import READ,WRITE,GLOBAL

FALSE=[('id','=',0)]


def config(env):
    return env['pe.mcp.config']._config()


class Config(models.Model):
    _inherit='pe.mcp.config'
    access_mode=fields.Selection([('pilot','Sample tasks read-only'),('organization','Organization business service')],required=True,default='pilot')
    org_id=fields.Integer(default=1,readonly=True)
    company_id=fields.Many2one('res.company',default=lambda self:self.env.company,readonly=True)
    continuous=fields.Boolean(default=False,string='Continuous access until revoked')
    security_checked_at=fields.Datetime(readonly=True)
    security_status=fields.Char(readonly=True)

    def _ready(self,*,enabled=True):
        if self.access_mode!='organization':return super()._ready(enabled=enabled)
        bridge=self.env.ref('platform_bridge.main').sudo()
        user=self.user_id
        return bool((self.enabled or not enabled) and self.org_id==1 and bridge.enabled
            and bridge.org_id==self.org_id and bridge.company_id==self.company_id
            and user and user.active and not user.pe_subject
            and user.has_group('platform_mcp_organization.group_writer')
            and not user.has_group('base.group_system') and not user.has_group('base.group_erp_manager')
            and user.company_id==self.company_id and user.company_ids==self.company_id)

    @api.constrains('access_mode','org_id','company_id','continuous','enabled','user_id')
    def _check_organization(self):
        for rec in self:
            if rec.access_mode=='organization' and (rec.org_id!=1 or rec.enabled and not rec._ready()):
                raise ValidationError('Organization MCP needs its dedicated local non-admin account and the unchanged organization-1 bridge/company binding.')

    def _monitor_security(self):
        cfg=self._config()
        if not cfg or cfg.access_mode!='organization':return
        ready=cfg._ready()
        status='Active; continuous until revoked' if ready and cfg.continuous else ('Active; expiring keys' if ready else 'Disabled or invalid authority; keys revoked')
        if not ready:
            self.env['muk_mcp.key'].sudo().search([('active','=',True)]).write({'active':False})
        cfg.write({'security_checked_at':fields.Datetime.now(),'security_status':status})


class Users(models.Model):
    _inherit='res.users'

    def _mcp_restriction(self,model,write=False):
        cfg=config(self.env)
        if cfg and cfg.access_mode=='organization' and self.has_group('platform_mcp_guard.group_reader'):
            return self._mcp_org_domain(model,write)
        if model not in ('project.task','project.project','project.task.type'):return []
        return super()._mcp_restriction(model,write)

    def _mcp_org_domain(self,model,write=False):
        cfg=config(self.env)
        if not cfg or not cfg._ready() or self.id!=cfg.user_id.id:return FALSE
        company=cfg.company_id.id
        if model=='res.company':return [('id','=',company)] if not write else FALSE
        if model=='res.users':return [('active','=',True),('id','!=',1),('share','=',False),('company_id','=',company),('pe_subject','=',False)] if not write else FALSE
        if model=='res.partner':
            shared=[('company_id','=',company),('mcp_shared','=',True),('pe_managed','=',False),('type','!=','private'),('user_ids','=',False)]
            parents=self.env['res.partner'].sudo().search(shared).ids+[self.partner_id.id,cfg.company_id.partner_id.id]
            shared+=[('parent_id','in',[False,*parents])]
            return shared if write else list(Domain(shared)|Domain('id','in',[self.partner_id.id,cfg.company_id.partner_id.id]))
        if model=='calendar.event':
            partners=self.env['res.partner'].with_user(self).search([]).ids
            return [('user_id','=',self.id),('partner_ids','not any',[('id','not in',partners)])]
        if model in ('ir.attachment','mail.message','mail.activity'):
            target_model,target_id=('model','res_id') if model=='mail.message' else ('res_model','res_id')
            terms=[]
            for name in WRITE:
                if name in self.env and name not in GLOBAL and name not in ('project.task.type','ir.attachment','mail.activity','mail.message'):
                    ids=self.env[name].with_user(self).search([]).ids
                    if ids:terms.append(Domain([(target_model,'=',name),(target_id,'in',ids)]))
            if model=='ir.attachment':
                terms.append(Domain([('res_model','=',False),('create_uid','=',self.id)]))
                return list(Domain.OR(terms)&Domain('company_id','=',company))
            return list(Domain.OR(terms))
        if model=='project.task.type':
            projects=self.env['project.project'].with_user(self).search([]).ids
            base=Domain([('user_id','in',[False,self.id]),('project_ids','not any',[('id','not in',projects)])])
            if write:base &= Domain(['|',('user_id','=',self.id),('project_ids','!=',False)])
            return list(base)
        if model in GLOBAL:
            domain=Domain('mcp_company_id','=',company) if write else Domain('mcp_company_id','in',[False,company])
            if 'company_id' in self.env[model]._fields:domain &= Domain('company_id','=',company) if write else Domain('company_id','in',[False,company])
            if 'team_id' in self.env[model]._fields:domain &= Domain(['|',('team_id','=',False),('team_id','access','read')])
            if model=='utm.campaign':domain &= Domain(['|',('user_id','=',False),('user_id.company_id','=',company)])
            if 'parent_id' in self.env[model]._fields and self.env[model]._fields['parent_id'].comodel_name==model:
                domain &= Domain(['|',('parent_id','=',False),('parent_id.mcp_company_id','in',[False,company])])
            return list(domain)
        if model not in READ:return FALSE
        target=self.env[model]
        if 'company_id' in target._fields:
            result=Domain('company_id','=',company)
            if model in ('product.template','product.product','product.pricelist','account.payment.term','crm.team') and not write:
                result=Domain('company_id','in',[False,company])
        elif 'company_ids' in target._fields:
            result=Domain([('company_ids','in',[company]),('company_ids','not any',[('id','!=',company)])])
        else:return [] if not write else FALSE
        if model=='project.project':
            result &= Domain(['|',('privacy_visibility','in',['employees','portal']),('allowed_internal_user_ids','in',[self.id])])
        if model=='project.task':
            result &= Domain(['|',('project_id','access','read'),'&',('project_id','=',False),('user_ids','in',[self.id])])
            result &= Domain(['|',('parent_id','=',False),'&',('parent_id.company_id','=',company),'|',('parent_id.project_id','access','read'),'&',('parent_id.project_id','=',False),('parent_id.user_ids','in',[self.id])])
        for field_name in READ[model]:
            field=target._fields.get(field_name)
            if field and field.type=='many2one' and field.comodel_name in READ and field.comodel_name not in (model,'res.users'):
                result &= Domain(['|',(field_name,'=',False),(field_name,'access','read')])
        if model=='sale.order.line':result &= Domain('order_id','access','read')
        if model=='account.move.line':result &= Domain('move_id','access','read')
        return list(result)


class Access(models.Model):
    _inherit='ir.access'

    def _get_access_context(self):
        yield from super()._get_access_context()
        cfg=config(self.env)
        yield (cfg.access_mode,cfg.org_id,cfg.company_id.id,cfg.continuous) if cfg else ('no-org-mcp',)


def key_expiry(self):
    cfg=config(self.env)
    return False if cfg and cfg.access_mode=='organization' and cfg.continuous else fields.Datetime.now()+timedelta(days=7)


class Keys(models.Model):
    _inherit='muk_mcp.key'
    expires_at=fields.Datetime(required=False,readonly=True,default=key_expiry)

    @api.constrains('user_id','scope','expires_at')
    def _check_guard(self):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._check_guard()
        for key in self:
            if key.user_id!=cfg.user_id or key.user_id.pe_subject or key.scope not in ('read','write'):
                raise ValidationError('Organization keys belong only to the dedicated local account.')
            if not key.expires_at and not cfg.continuous:
                raise ValidationError('A key without expiry requires explicit continuous organization access.')
            if key.expires_at and key.expires_at>fields.Datetime.now()+timedelta(days=7,seconds=5):
                raise ValidationError('Use a continuous key or a bounded temporary key, not an extendable distant expiry.')

    def _authenticate_domain(self):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._authenticate_domain()
        if not cfg._ready():return Domain('id','=',0)
        expiry=Domain('expires_at','>',fields.Datetime.now())
        if cfg.continuous:expiry|=Domain('expires_at','=',False)
        return Domain([('user_id','=',cfg.user_id.id),('user_id.active','=',True),('scope','in',['read','write'])])&expiry

    def write(self,vals):
        result=super().write(vals)
        if vals.get('active') is False:
            self.env['muk_mcp.transfer'].sudo().search([('mcp_key_id','in',self.ids),('used','=',False)]).write({'used':True})
        return result


class Allowlist(models.Model):
    _inherit='muk_mcp_access.model'

    def _is_model_allowed(self,model_name,category='read'):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._is_model_allowed(model_name,category)
        if model_name not in READ or category=='write' and model_name not in WRITE:return False
        domain=[('model_name','=',model_name),('allow_read','=',True),('domain','!=',False)]
        if category=='write':domain.append(('allow_write','=',True))
        return bool(self._entries().search_count(domain))

    def _get_allowed_model_names(self,category):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._get_allowed_model_names(category)
        return {model for model in READ if model in self.env and self._is_model_allowed(model,category)}

    @api.constrains('model_id','allow_write','domain')
    def _check_narrow_allowlist(self):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._check_narrow_allowlist()
        for entry in self:
            if entry.model_name not in READ or entry.allow_write and entry.model_name not in WRITE or not entry.domain:
                raise ValidationError('Organization MCP requires an explicit business-model domain and approved operations.')


class Partner(models.Model):
    _inherit='res.partner'
    mcp_shared=fields.Boolean(default=False,copy=False,readonly=True,string='Shared organization MCP business contact')


class Attachment(models.Model):
    _inherit='ir.attachment'

    @api.model_create_multi
    def create(self,vals_list):
        cfg=config(self.env)
        if cfg and cfg.access_mode=='organization' and self.env.uid==cfg.user_id.id and not self.env.su:
            vals_list=[dict(v,company_id=cfg.company_id.id) for v in vals_list]
        return super().create(vals_list)


class Transfer(models.Model):
    _inherit='muk_mcp.transfer'
    mcp_key_id=fields.Many2one('muk_mcp.key',ondelete='cascade',readonly=True)

    def _receive(self,data):
        from .policy import MAX_FILE_BYTES
        if len(data)>MAX_FILE_BYTES:raise AccessError('Transfer exceeds five MiB.')
        return super()._receive(data)

    @api.model
    def _issue(self,operation,**values):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._issue(operation,**values)
        cfg._assert_reader(self.env.user)
        key=getattr(request,'_mcp_key',None) if request else None
        if not key or not key.active or key.user_id!=cfg.user_id or operation=='upload' and key.scope!='write':
            raise AccessError('A current scoped MCP key is required for a transfer.')
        return super()._issue(operation,mcp_key_id=key.id,**values)

    @api.model
    def _redeem(self,token,operation):
        cfg=config(self.env)
        candidate=self.sudo().search([('token_hash','=',self.env['muk_mcp.key']._hash_key(token)),('operation','=',operation)],limit=1)
        key=candidate.mcp_key_id
        if (not cfg or cfg.access_mode!='organization' or not cfg._ready() or not key or not key.active
            or key.user_id!=cfg.user_id or key.expires_at and key.expires_at<=fields.Datetime.now()
            or not key.expires_at and not cfg.continuous):return self.browse()
        return super()._redeem(token,operation)


# These upstream reference models have no company column. New MCP-created
# references are explicitly organization-owned; existing global rows stay read-only.
for _model in GLOBAL:
    globals()['Scoped'+_model.replace('.','_')]=type('Scoped'+_model.replace('.','_'),(models.Model,),{
        '__module__':__name__,'_inherit':_model,
        'mcp_company_id':fields.Many2one('res.company',index=True,copy=False,readonly=True),
    })
