"""Default-deny, expiring, field-bounded access to designated TEST tasks only."""
from datetime import timedelta
from copy import deepcopy
import hashlib,json

from odoo import api,fields,models
from odoo.exceptions import AccessDenied,AccessError,ValidationError
from odoo.fields import Domain
from odoo.addons.muk_mcp.core.tool import get_tool_index

TOOLS=frozenset(('search_read','read_records','search_count'))
FIELDS=frozenset(('id','name','display_name','state','priority','stage_id','project_id','date_deadline'))
DEFAULT_FIELDS=['id','name','state','priority','stage_id']
TTL_DAYS=7


class Config(models.Model):
    _name='pe.mcp.config'
    _description='Scoped TEST MCP access'
    name=fields.Char(default='Odoo TEST MCP',required=True)
    enabled=fields.Boolean(default=False)
    user_id=fields.Many2one('res.users',ondelete='restrict')
    project_id=fields.Many2one('project.project',ondelete='restrict')
    task_ids=fields.Many2many('project.task')

    @api.model
    def _config(self):
        return self.env.ref('platform_mcp_guard.main',raise_if_not_found=False).sudo() if self.env.ref('platform_mcp_guard.main',raise_if_not_found=False) else self.browse()

    def _ready(self,*,enabled=True):
        self.ensure_one()
        user=self.user_id
        return bool((self.enabled or not enabled) and user and user.active and not user.pe_subject
            and user.has_group('platform_mcp_guard.group_reader') and not user.has_group('base.group_system')
            and self.project_id and self.project_id.active
            and self.project_id.name.startswith('[נתוני בדיקה]')
            and self.project_id.company_id==user.company_id and user.company_ids==user.company_id
            and 0<len(self.task_ids)<=20
            and all(t.active and t.project_id==self.project_id and t.name.startswith('[בדיקה]')
                    and len(t.name)<=256 and not t.partner_id for t in self.task_ids))

    @api.constrains('enabled','user_id','project_id','task_ids')
    def _check_ready(self):
        for config in self:
            if config.enabled and not config._ready():
                raise ValidationError('Choose an active local reader and explicitly designated test project/tasks before enabling MCP.')

    def _assert_reader(self,user):
        self.ensure_one()
        if not self._ready() or user.id!=self.user_id.id:
            raise AccessError('MCP access is disabled or outside its configured reader scope.')

    def _domain(self):
        return [('project_id','=',self.project_id.id),('id','in',self.task_ids.ids)]


class Users(models.Model):
    _inherit='res.users'

    def _mcp_restriction(self,model,write=False):
        if not self.has_group('platform_mcp_guard.group_reader'):
            return []
        if write:return [('id','=',0)]
        if model=='project.task':return self._mcp_task_domain()
        if model=='project.project':return [('id','=',self._mcp_project_id())]
        return [('id','in',self._mcp_stage_ids())]

    def write(self,vals):
        group=self.env.ref('platform_mcp_guard.group_reader',raise_if_not_found=False)
        readers=self.sudo().filtered(lambda user:group in user.group_ids) if group and vals.get('active') is False else self.browse()
        result=super().write(vals)
        if readers:
            self.env['muk_mcp.key'].sudo().with_context(active_test=False).search([('user_id','in',readers.ids)]).write({'active':False})
        return result

    def _mcp_task_domain(self):
        config=self.env['pe.mcp.config']._config()
        return config._domain() if config and config._ready() and self.id==config.user_id.id else [('id','=',0)]

    def _mcp_project_id(self):
        config=self.env['pe.mcp.config']._config()
        return config.project_id.id if config and config._ready() and self.id==config.user_id.id else 0

    def _mcp_stage_ids(self):
        config=self.env['pe.mcp.config']._config()
        return config.task_ids.stage_id.ids if config and config._ready() and self.id==config.user_id.id else []

    def _check_credentials(self,credential,user_agent_env):
        if self.sudo().has_group('platform_mcp_guard.group_reader'):
            raise AccessDenied('This account is restricted to its scoped MCP key.')
        return super()._check_credentials(credential,user_agent_env)


class Access(models.Model):
    _inherit='ir.access'

    def _get_access_context(self):
        yield from super()._get_access_context()
        config=self.env['pe.mcp.config']._config()
        yield hashlib.sha256(json.dumps((config.enabled,config.user_id.id,config.project_id.id,config.task_ids.ids,config._ready())).encode()).hexdigest() if config else 'mcp-disabled'


class Keys(models.Model):
    _inherit='muk_mcp.key'
    scope=fields.Selection(default='read')
    expires_at=fields.Datetime(required=True,readonly=True,default=lambda self:fields.Datetime.now()+timedelta(days=TTL_DAYS))

    @api.constrains('user_id','scope','expires_at')
    def _check_guard(self):
        config=self.env['pe.mcp.config']._config()
        now=fields.Datetime.now()
        for key in self:
            if (not config or key.user_id!=config.user_id or key.user_id.pe_subject or key.scope!='read'
                    or not key.expires_at or key.expires_at>now+timedelta(days=TTL_DAYS,seconds=5)):
                raise ValidationError('MCP keys must belong to the configured local reader, be read-only, and expire within seven days.')

    def write(self,vals):
        if {'scope','user_id','key_hash','key_prefix'}.intersection(vals):
            raise AccessError('MCP key identity and scope are immutable; rotate the key.')
        if vals.get('active') and any(not key.active for key in self):
            raise AccessError('A revoked MCP key cannot be re-enabled; issue a new key.')
        if 'expires_at' in vals and any(not vals['expires_at'] or fields.Datetime.to_datetime(vals['expires_at'])>key.expires_at for key in self):
            raise AccessError('MCP key expiry cannot be extended; rotate the key.')
        return super().write(vals)

    def _authenticate_domain(self):
        config=self.env['pe.mcp.config']._config()
        if not config or not config._ready():return Domain('id','=',0)
        return (super()._authenticate_domain() & Domain('user_id','=',config.user_id.id)
                & Domain('scope','=','read') & Domain('expires_at','>',fields.Datetime.now()))

    @api.model
    def generate_playground_key(self,name=None,scope='read'):
        if scope!='read':raise AccessError('Only read-only MCP keys are allowed.')
        return super().generate_playground_key(name,scope)


class KeyWizard(models.TransientModel):
    _inherit='muk_mcp.generate_key'
    scope=fields.Selection(default='read')


class ModelAllowlist(models.Model):
    _inherit='muk_mcp_access.model'

    def _is_active(self):return True

    def _is_model_allowed(self,model_name,category='read'):
        if model_name!='project.task' or category!='read':return False
        return bool(self._entries().search_count([('model_name','=',model_name),('allow_read','=',True),('allow_write','=',False),('domain','!=',False)]))

    def _get_allowed_model_names(self,category):
        return {'project.task'} if self._is_model_allowed('project.task',category) else set()

    @api.constrains('model_id','allow_write','domain')
    def _check_narrow_allowlist(self):
        for entry in self:
            if entry.model_name!='project.task' or entry.allow_write or not entry.domain:
                raise ValidationError('MCP permits only an explicit read-only project.task record domain.')


class Tool(models.Model):
    _inherit='muk_mcp.tool'

    def get_tools(self,registry=None):
        index=get_tool_index(self.env,registry=registry)
        result=[]
        for tool in super().get_tools(registry):
            if tool['name'] not in TOOLS or index[tool['name']]['kind']!='method':continue
            tool=deepcopy(tool);schema=tool['inputSchema'];props=schema['properties']
            props.pop('context',None)
            schema['required']=[k for k in schema.get('required',[]) if k!='context']
            props['model']={'type':'string','enum':['project.task']}
            if 'fields' in props:props['fields']={'type':'array','items':{'type':'string','enum':sorted(FIELDS)},'minItems':1,'maxItems':len(FIELDS)}
            if 'limit' in props:props['limit']={'type':'integer','minimum':1,'maximum':20,'default':20}
            if 'domain' in props:props['domain']={'type':'array','maxItems':12,'default':[]}
            if 'ids' in props:props['ids']={'type':'array','items':{'type':'integer','minimum':1},'minItems':1,'maxItems':20}
            tool['description']='Read only explicitly allowed TEST tasks. '+tool['description']
            result.append(tool)
        return result

    def _call(self,name,arguments,env,enforce_scope=None):
        config=env['pe.mcp.config']._config()
        if not config:raise AccessError('MCP is disabled.')
        config._assert_reader(env.user)
        index=get_tool_index(env).get(name)
        if name not in TOOLS or not index or index['kind']!='method':
            raise AccessError('This MCP tool is not allowed.')
        data=dict(arguments) if isinstance(arguments,dict) else {}
        allowed={'model','domain'} if name=='search_count' else ({'model','ids','fields'} if name=='read_records' else {'model','domain','fields','limit','offset','order'})
        if set(data)-allowed or data.get('model')!='project.task':raise AccessError('MCP arguments are outside the allowed scope.')
        if name!='search_count':
            selected=data.get('fields',DEFAULT_FIELDS)
            if not isinstance(selected,list) or not selected or any(not isinstance(v,str) or v not in FIELDS for v in selected):raise AccessError('MCP field is not allowed.')
            data['fields']=selected
        if name=='read_records':
            ids=data.get('ids')
            if not isinstance(ids,list) or not 0<len(ids)<=20 or any(type(v)is not int or v<=0 for v in ids):raise AccessError('Invalid bounded MCP record list.')
        else:
            domain=data.get('domain',[])
            if not isinstance(domain,list) or len(domain)>12:raise AccessError('Invalid bounded MCP domain.')
            for leaf in domain:
                if leaf in ('&','|','!'):continue
                if not isinstance(leaf,list) or len(leaf)!=3 or leaf[0] not in ('id','project_id','state','name','priority') or leaf[1] not in ('=','!=','in','not in','ilike','not ilike'):
                    raise AccessError('MCP domain is outside its allowed fields/operators.')
                values=leaf[2] if isinstance(leaf[2],list) else [leaf[2]]
                if len(values)>20 or any(type(v) not in (str,int,bool) or isinstance(v,str) and len(v)>120 for v in values):raise AccessError('MCP domain value exceeds its bounds.')
            if name=='search_read':
                if type(data.get('limit',20))is not int or not 1<=data.get('limit',20)<=20 or type(data.get('offset',0))is not int or not 0<=data.get('offset',0)<=100:
                    raise AccessError('MCP page is outside its bounds.')
                if data.get('order','id') not in ('id','id asc','id desc','name','name asc','name desc'):raise AccessError('MCP ordering is not allowed.')
                data.setdefault('limit',20)
        return super()._call(name,data,env,enforce_scope='read')


class Mixin(models.AbstractModel):
    _inherit='muk_mcp.mixin'

    def _resolve_model(self,model):
        if model!='project.task':raise AccessError('MCP model is not allowed.')
        return super()._resolve_model(model)

    def _mcp_apply_domain(self,model,domain):
        config=self.env['pe.mcp.config']._config();config._assert_reader(self.env.user)
        return list(Domain(super()._mcp_apply_domain(model,domain) or []) & Domain(config._domain()))

    def _mcp_assert_records_allowed(self,model,ids):
        config=self.env['pe.mcp.config']._config();config._assert_reader(self.env.user)
        if model!='project.task' or set(ids)-set(config.task_ids.ids):raise AccessError('MCP record is outside the allowed test tasks.')
        return super()._mcp_assert_records_allowed(model,ids)


class Log(models.Model):
    _inherit='muk_mcp.log'

    @api.model
    def log(self,**values):
        values.pop('request_data',None);values.pop('response_data',None)
        if values.get('error_message'):values['error_message']='Scoped MCP request denied or failed.'
        return super().log(**values)
