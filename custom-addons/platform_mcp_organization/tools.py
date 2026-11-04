import json,re
from copy import deepcopy
from odoo import api,models,release
from odoo.exceptions import AccessError
from odoo.fields import Domain
from odoo.addons.muk_mcp.models.tool import MCPTool
from odoo.addons.muk_mcp.core.tool import get_tool_index
from odoo.addons.muk_mcp_access.mcp.mixin import MCPMixin as AccessMixin
from odoo.addons.muk_mcp.tools.parser import coerce_json_value
from .models import config
from .policy import TOOLS,READ,WRITE,GLOBAL,METHODS,MAX_RECORDS,MAX_FILE_BYTES

QUIET=dict(tracking_disable=True,mail_create_nolog=True,mail_create_nosubscribe=True,
    mail_auto_subscribe_no_notify=True,mail_notify_force_send=False,no_reset_password=True)


def field_names(env,model,write=False):
    allowed=WRITE.get(model,set()) if write else READ.get(model,set())
    return allowed&set(env[model].fields_get())


def assert_records(env,model,ids,write=False):
    if model not in READ or model not in env or not isinstance(ids,list) or not 0<len(ids)<=MAX_RECORDS or any(type(i)is not int or i<=0 for i in ids):
        raise AccessError('Invalid business record selection.')
    domain=Domain('id','in',ids)&Domain(env.user._mcp_org_domain(model,write))
    found=env[model].with_context(active_test=False).search(domain)
    if set(found.ids)!=set(ids):raise AccessError('Record is outside the organization or its business/privacy permissions.')
    found.check_access('write' if write else 'read')
    return found


def values(env,model,data,create=False):
    if model not in WRITE or not isinstance(data,dict) or len(data)>50 or set(data)-field_names(env,model,True):
        raise AccessError('Fields are outside the approved business write surface.')
    result=dict(data)
    for name,value in data.items():
        field=env[model]._fields[name]
        if field.type=='many2one' and value:
            assert_records(env,field.comodel_name,[value])
        if field.type in ('many2many','one2many'):
            if not isinstance(value,list) or len(value)>MAX_RECORDS:raise AccessError('Invalid bounded relation commands.')
            commands=[]
            for command in value:
                if not isinstance(command,(list,tuple)) or not command or type(command[0])is not int:raise AccessError('Invalid relation command.')
                op=command[0];target=field.comodel_name
                if op==0 and len(command)==3:
                    commands.append((0,0,values(env,target,command[2],True)))
                elif op==1 and len(command)==3:
                    assert_records(env,target,[command[1]],True)
                    commands.append((1,command[1],values(env,target,command[2])))
                elif op in (2,3,4) and len(command)>=2:
                    assert_records(env,target,[command[1]],op==2)
                    commands.append((op,command[1],0))
                elif op==5:commands.append((5,0,0))
                elif op==6 and len(command)==3 and isinstance(command[2],list):
                    if command[2]:assert_records(env,target,command[2])
                    commands.append((6,0,command[2]))
                else:raise AccessError('Relation command is not permitted.')
            result[name]=commands
    cfg=config(env)
    if model=='calendar.event' and result.get('user_id',env.uid)!=env.uid:
        raise AccessError('The integration calendar belongs to the service account, not another user.')
    if create:
        if 'company_id' in env[model]._fields:result['company_id']=cfg.company_id.id
        if 'company_ids' in env[model]._fields:result['company_ids']=[(6,0,cfg.company_id.ids)]
        if model in GLOBAL:result['mcp_company_id']=cfg.company_id.id
        if model=='res.partner':result.update(mcp_shared=True,type=result.get('type','contact'))
        if model=='res.partner' and result['type']=='private':raise AccessError('MCP business contacts cannot be private addresses.')
        if model=='project.project':result.setdefault('privacy_visibility','employees')
        if model=='project.task.type' and not result.get('project_ids'):result['user_id']=env.uid
        if model=='calendar.event':result['user_id']=env.uid
    if model=='res.partner' and result.get('type')=='private':raise AccessError('Private addresses are outside organization MCP.')
    return result


class Tool(models.Model):
    _inherit='muk_mcp.tool'

    def get_tools(self,registry=None):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super().get_tools(registry)
        index=get_tool_index(self.env,registry=registry)
        result=[]
        for tool in MCPTool.get_tools(self,registry):
            if tool['name'] not in TOOLS or index[tool['name']]['kind']!='method':continue
            tool=deepcopy(tool);schema=tool['inputSchema'];props=schema['properties']
            props.pop('context',None);schema['required']=[v for v in schema.get('required',[]) if v!='context']
            schema['additionalProperties']=False
            if 'model' in props:props['model']={'type':'string','enum':sorted(m for m in READ if m in self.env)}
            if 'domain' in props:props['domain']={'type':'array','items':{},'maxItems':40,'default':[]}
            if 'limit' in props:props['limit']={'type':'integer','minimum':1,'maximum':MAX_RECORDS,'default':50}
            if 'offset' in props:props['offset']={'type':'integer','minimum':0,'maximum':10000,'default':0}
            if 'order' in props:props['order']={'type':'string','default':'id'}
            if 'ids' in props:props['ids']={'type':'array','items':{'type':'integer','minimum':1},'minItems':1,'maxItems':MAX_RECORDS}
            tool['description']='Organization-1 business service; private user contacts and technical administration excluded. '+tool['description']
            result.append(tool)
        return result

    def _call(self,name,arguments,env,enforce_scope=None):
        cfg=config(env)
        if not cfg or cfg.access_mode!='organization':return super()._call(name,arguments,env,enforce_scope)
        cfg._assert_reader(env.user)
        return MCPTool._call(self,name,arguments,env,enforce_scope=enforce_scope)

    def _execute(self,name,arguments,env,enforce_scope):
        cfg=config(env)
        if not cfg or cfg.access_mode!='organization':return super()._execute(name,arguments,env,enforce_scope)
        cfg._assert_reader(env.user)
        entry=get_tool_index(env).get(name)
        if name not in TOOLS or not entry or entry['kind']!='method':raise AccessError('Tool is not a reviewed built-in business tool.')
        self._check_scope(entry['category'],enforce_scope)
        args=dict(arguments)
        allowed=set(entry['input_schema'].get('properties',{}))-{'context'}
        if set(args)-allowed:raise AccessError('Caller context or unsupported arguments are forbidden.')
        env=env(context={**env.context,**QUIET,'allowed_company_ids':cfg.company_id.ids,'mcp_tool_category':entry['category']})
        model=args.get('model')
        if model:
            if model not in READ or model not in env:raise AccessError('Model is outside the organization business surface.')
            if not env['muk_mcp_access.model']._is_model_allowed(model,entry['category']):raise AccessError('The explicit model policy denies this operation.')
        if model and 'ids' in args:
            assert_records(env,model,args['ids'],entry['category']=='write')
        if model and args.get('id'):assert_records(env,model,[args['id']],entry['category']=='write')
        if 'fields' in args or name in ('search_read','read_records','describe_model'):
            selected=args.get('fields') or sorted(field_names(env,model))
            if not isinstance(selected,list) or not selected or len(selected)>100:raise AccessError('Invalid field list.')
            for field in selected:
                stem=field.split(':')[0] if name=='read_group' else field
                if stem not in field_names(env,model) or '/' in field or '.' in field:raise AccessError('Field is private or outside the business surface.')
                if ':' in field and (name!='read_group' or field.split(':')[1] not in ('sum','avg','min','max','count','count_distinct')):
                    raise AccessError('Aggregate is not permitted.')
            args['fields']=selected
        if 'groupby' in args:
            groupby=args['groupby'] if isinstance(args['groupby'],list) else [args['groupby']]
            if len(groupby)>5 or any(not isinstance(v,str) or v.split(':')[0] not in field_names(env,model) for v in groupby):raise AccessError('Invalid grouping.')
        if 'domain' in args:
            domain=coerce_json_value(args['domain']) or []
            if not isinstance(domain,list) or len(domain)>40:raise AccessError('Invalid bounded query domain.')
            for leaf in domain:
                if isinstance(leaf,str) and leaf in ('&','|','!'):continue
                if not isinstance(leaf,(list,tuple)) or len(leaf)!=3 or leaf[0] not in field_names(env,model) or leaf[1] not in ('=','!=','>','<','>=','<=','in','not in','ilike','not ilike','like','=ilike','child_of','parent_of'):
                    raise AccessError('Relational traversal or unsupported domain is forbidden.')
                raw=leaf[2] if isinstance(leaf[2],list) else [leaf[2]]
                if len(raw)>MAX_RECORDS or any(type(v) not in (str,int,float,bool,type(None)) or isinstance(v,str) and len(v)>512 for v in raw):raise AccessError('Invalid domain value.')
            args['domain']=domain
        if name in ('search_read','read_group','export_records','get_messages','list_models'):
            cap=MAX_RECORDS
            if type(args.get('limit',50))is not int or not 1<=args.get('limit',50)<=cap:raise AccessError('Query page exceeds its bound.')
            args.setdefault('limit',50)
        if type(args.get('offset',0))is not int or not 0<=args.get('offset',0)<=10000:raise AccessError('Offset exceeds its bound.')
        if args.get('order'):
            for part in args['order'].split(','):
                match=re.fullmatch(r'\s*([a-z_][a-z0-9_]*)(?:\s+(asc|desc))?\s*',part)
                if not match or match[1] not in field_names(env,model):raise AccessError('Invalid ordering.')
        if name in ('create_records','update_records'):
            args['values']=values(env,model,args.get('values'),name=='create_records')
        if name=='delete_records' and model not in WRITE:raise AccessError('This model is read-only.')
        if name=='call_method':
            if args.get('method') not in METHODS.get(model,set()) or not args.get('ids') or (coerce_json_value(args.get('args')) or []) or args.get('kwargs'):
                raise AccessError('Only reviewed record business actions without arbitrary arguments are allowed.')
        if name=='schedule_activity' and args.get('user_id'):
            assert_records(env,'res.users',[args['user_id']])
        if name=='print_report':
            report=env['muk_mcp.mixin']._resolve_report(args.get('report_ref'))
            if not report or report.model not in ('sale.order','account.move'):
                raise AccessError('Only reviewed sale and invoice business reports are enabled.')
            assert_records(env,report.model,args['ids'])
        if name=='authorize_upload' and (type(args.get('size',0))is not int or not 0<=args.get('size',0)<=MAX_FILE_BYTES):
            raise AccessError('Upload size exceeds five MiB.')
        if name=='upload_file':
            if model and args.get('field') and args['field'] not in field_names(env,model,True):raise AccessError('Binary destination is not writable.')
            if len(args.get('text','').encode())>MAX_FILE_BYTES or len(args.get('data',''))>MAX_FILE_BYTES*4//3+8:raise AccessError('Upload size exceeds five MiB.')
        special=None
        if name=='list_models':
            special=[{'model':m,'description':env[m]._description} for m in sorted(READ) if m in env and args.get('search','').lower() in m.lower() and env['muk_mcp_access.model']._is_model_allowed(m)][:args['limit']]
        elif name=='describe_model':
            special=env[model].fields_get(args['fields'],['string','type','required','readonly','relation','selection','help'])
        elif name=='get_access_rights':
            special={'model':model,'current_user_rights':{op:bool(env[model].has_access(op)) and (op=='read' or model in WRITE) for op in ('read','write','create','unlink')},'organization':cfg.org_id,'privacy':'Business scope only; private user contacts excluded'}
        elif name=='whoami':
            special={'user_id':env.uid,'name':env.user.name,'organization':cfg.org_id,'company_id':cfg.company_id.id,'company_name':cfg.company_id.name,'identity':'Dedicated organization business service; not a personal SSO session','continuous_access':cfg.continuous}
        elif name=='system_info':
            special={'product':release.product_name,'version':release.version,'edition':'community','base_url':'https://odoo.test.ai-data-platform.com','organization':cfg.org_id,'company_id':cfg.company_id.id}
        if name=='list_modules' and args.get('state','installed')!='installed':raise AccessError('Only installed application discovery is exposed.')
        if special is not None:return self._serialize_result(special),{},model
        return MCPTool._execute(self,name,args,env,enforce_scope)


class Mixin(models.AbstractModel):
    _inherit='muk_mcp.mixin'

    def _resolve_model(self,model):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._resolve_model(model)
        cfg._assert_reader(self.env.user)
        if model not in READ:raise AccessError('Technical or private model is not exposed.')
        return AccessMixin._resolve_model(self,model)

    def _mcp_apply_domain(self,model,domain):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._mcp_apply_domain(model,domain)
        base=AccessMixin._mcp_apply_domain(self,model,domain)
        return list(Domain(base or [])&Domain(self.env.user._mcp_org_domain(model,self.env.context.get('mcp_tool_category')=='write')))

    def _mcp_assert_records_allowed(self,model,ids):
        cfg=config(self.env)
        if not cfg or cfg.access_mode!='organization':return super()._mcp_assert_records_allowed(model,ids)
        if ids:assert_records(self.env,model,list(ids),self.env.context.get('mcp_tool_category')=='write')
        return AccessMixin._mcp_assert_records_allowed(self,model,ids)

    def _resolve_resource_target(self,uri):
        record,field=super()._resolve_resource_target(uri)
        cfg=config(self.env)
        if cfg and cfg.access_mode=='organization':
            if record._name=='ir.attachment':assert_records(self.env,'ir.attachment',record.ids)
            elif field not in field_names(self.env,record._name):raise AccessError('Binary field is outside approved business fields.')
            if record._name!='ir.attachment':assert_records(self.env,record._name,record.ids)
        return record,field

    def _resolve_resource_uri(self,uri):
        result=super()._resolve_resource_uri(uri)
        cfg=config(self.env)
        if cfg and cfg.access_mode=='organization' and len(result[1])>MAX_FILE_BYTES:raise AccessError('Resource exceeds five MiB.')
        return result
