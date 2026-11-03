"""Bounded synchronization with explicit identity, CAS and durable operation IDs."""
from datetime import timedelta
from html import escape
import re
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessDenied, AccessError, UserError
from ..sync_policy import FIELDS, values, digest, reconcile, merged_baseline
from .bridge import BridgeRejected

QUIET=dict(tracking_disable=True,mail_create_nolog=True,mail_create_nosubscribe=True,
           mail_auto_subscribe_no_notify=True,no_reset_password=True)


def local_values(partner,base=None,*,profile=False):
    first,last=partner.pe_first_name or '',partner.pe_last_name or ''
    name=(first+' '+last).strip()
    # Odoo's ordinary full-name editor has no safe implicit name split.
    # Preserve its complete text rather than guessing a family-name boundary.
    if partner.name and partner.name!=name:
        old_name=((base or {}).get('first_name','')+' '+(base or {}).get('last_name','')).strip()
        if partner.name!=old_name:first,last=partner.name,''
    return values(dict(first_name=first or partner.name or '',last_name=last,
        email=partner.email or '',phone=partner.phone or '',mobile_phone='' if profile else partner.pe_mobile_phone or '',
        company='' if profile else partner.pe_company or ''))


def local_signature(partner,base=None,*,profile=False):
    return digest(dict(fields=local_values(partner,base,profile=profile),
        write_date=str(partner.write_date),active=partner.active,owner=partner.pe_owner_id.id,
        visibility=partner.pe_visibility,contact_key=partner.pe_contact_key or None))


def local_patch(data,*,profile=False):
    patch=dict(name=(data['first_name']+' '+data['last_name']).strip(),
        pe_first_name=data['first_name'],pe_last_name=data['last_name'],
        email=data['email'] or False,phone=data['phone'] or False)
    if not profile:patch.update(pe_mobile_phone=data['mobile_phone'] or False,pe_company=data['company'] or False)
    return patch


def locked(partner):
    partner.env.cr.execute('SELECT id FROM res_partner WHERE id=%s FOR UPDATE',[partner.id])
    partner.invalidate_recordset()
    return partner


def proof_for(user):
    return dict(org=user.pe_org_id,user=user.pe_user_id,epoch=user.pe_native_epoch,
        access_version=user.pe_access_version,sub=user.pe_subject)


def validate_proof(user,proof):
    if not isinstance(proof,dict) or proof!=proof_for(user):
        raise AccessDenied('Platform authority changed; sign in again')


class Users(models.Model):
    _inherit='res.users'

    pe_profile_base=fields.Json(copy=False,groups=fields.NO_ACCESS)
    pe_profile_review=fields.Json(copy=False,groups=fields.NO_ACCESS)
    pe_scan_after=fields.Integer(default=0,copy=False,groups=fields.NO_ACCESS)
    pe_next_sync_at=fields.Datetime(default=fields.Datetime.now,copy=False,groups=fields.NO_ACCESS)
    pe_sync_result=fields.Json(copy=False,groups=fields.NO_ACCESS)

    def _pe_link_domain(self):
        self.ensure_one()
        if not self.sudo().pe_subject:
            return [] if self.has_group('base.group_system') else [('id','=',0)]
        scope=self._pe_identity()
        from odoo.fields import Domain
        privacy=Domain.OR([Domain([('visibility','=','org')]),Domain([('owner_id','=',self.id)])])
        return [('org_id','=',scope['org']),('contact_key','in',[str(v) for v in scope['read_contact_ids']])]+list(privacy)


class Link(models.Model):
    _name='pe.bridge.link'
    _description='Platform contact synchronization'
    _order='write_date desc,id'

    name=fields.Char(readonly=True)
    org_id=fields.Integer(required=True,index=True,readonly=True)
    contact_key=fields.Char(required=True,index=True,readonly=True)
    partner_id=fields.Many2one('res.partner',ondelete='restrict',readonly=True)
    owner_id=fields.Many2one('res.users',readonly=True)
    visibility=fields.Selection([('private','Private'),('org','Organization')],required=True,readonly=True)
    source_scope=fields.Json(copy=False,groups=fields.NO_ACCESS)
    base=fields.Json(copy=False,groups=fields.NO_ACCESS)
    source_version=fields.Char(copy=False,readonly=True)
    conflicts=fields.Json(copy=False,groups=fields.NO_ACCESS)
    status=fields.Selection([(v,v.replace('_',' ').title()) for v in
        ('synchronized','conflict','duplicate_review','scope_review','source_paused','local_archive_review','invalid_fields')],readonly=True)
    review_details=fields.Html(compute='_compute_review_details',compute_sudo=False,sanitize=True)
    _unique_contact=models.Constraint('unique(org_id,contact_key)','Platform contact binding must be unique')
    _unique_partner=models.Constraint('unique(partner_id)','One partner can have only one Platform contact binding')

    def _compute_review_details(self):
        for link in self:
            data=link.sudo().conflicts or {}
            rows=[]
            for name,versions in data.items():
                if not isinstance(versions,dict):continue
                cells=[escape(str(versions.get(k,''))) for k in ('base','platform','odoo')]
                rows.append('<tr><th>'+escape(name)+'</th>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
            link.review_details=('<table class="table"><thead><tr><th>Field</th><th>Previous agreement</th>'
                '<th>Platform Engineer</th><th>Odoo</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>') if rows else False

    @api.model
    def _owner(self,user,source):
        return self.env['res.users'].sudo().search([('pe_org_id','=',user.pe_org_id),
            ('pe_user_id','=',source['owner']),('pe_subject','!=',False)],limit=1) if source.get('owner') else self.env['res.users']

    @api.model
    def _validate_source(self,user,source):
        if (not isinstance(source,dict) or source.get('org')!=user.pe_org_id
                or type(source.get('id')) is not int or source['id']<=0
                or source.get('visibility') not in ('private','org')
                or not isinstance(source.get('version'),str) or not re.fullmatch('[0-9a-f]{64}',source['version'])):
            raise AccessDenied('Invalid source contact binding')
        if source['visibility']=='private' and source.get('owner')!=user.pe_user_id:
            raise AccessDenied('Foreign private contact refused')
        return values(source.get('fields'))

    @api.model
    def _consume(self,user,source,*,dry_run=False):
        data=self._validate_source(user,source)
        link=self.sudo().search([('org_id','=',user.pe_org_id),('contact_key','=',str(source['id']))],limit=1)
        if not source.get('active') or source.get('merged_into'):
            if link and not dry_run:link.write({'status':'source_paused'})
            return 'source_paused'
        owner=self._owner(user,source)
        source_scope={k:source.get(k) for k in ('org','owner','visibility')}
        if not link:
            # Unclassified Odoo records are never adopted by matching an email.
            candidates=self.env['res.partner'].sudo().search([('pe_managed','=',False),
                ('user_ids','=',False),('id','!=',user.company_id.partner_id.id),
                '|',('email','=',data['email'] or '__no_email__'),('phone','=',data['phone'] or '__no_phone__')],limit=2)
            if candidates:
                if not dry_run:self.sudo().create(dict(name=(data['first_name']+' '+data['last_name']).strip(),
                    org_id=user.pe_org_id,contact_key=str(source['id']),owner_id=owner.id,visibility=source['visibility'],
                    source_scope=source_scope,source_version=source['version'],status='duplicate_review',
                    conflicts={'identity':dict(base='Unclassified local contact',platform='Stable source ID '+str(source['id']),odoo='Manual link review required')}))
                return 'duplicate_review'
            if dry_run:return 'would_create'
            partner=self.env['res.partner'].sudo().with_context(**QUIET).create(dict(local_patch(data),
                company_id=user.company_id.id,pe_managed=True,pe_org_id=user.pe_org_id,
                pe_contact_key=str(source['id']),pe_owner_id=owner.id,pe_visibility=source['visibility'],
                pe_opt_out=bool(source.get('opt_out'))))
            self.sudo().create(dict(name=partner.name,org_id=user.pe_org_id,contact_key=str(source['id']),
                partner_id=partner.id,owner_id=owner.id,visibility=source['visibility'],source_scope=source_scope,
                source_version=source['version'],base=data,status='synchronized'))
            return 'created'
        if not link.partner_id or link.status=='duplicate_review':return 'duplicate_review'
        partner=link.partner_id.sudo()
        if self.env['pe.bridge.operation'].sudo().search_count([('partner_id','=',partner.id),('state','=','review')]):
            return 'operation_review'
        if not partner.active:
            if not dry_run:link.write({'status':'local_archive_review'})
            return 'local_archive_review'
        right=local_values(partner,link.base)
        if link.source_scope!=source_scope:
            if right!=link.base and right!=data:
                if not dry_run:link.write({'status':'scope_review','conflicts':{
                    k:dict(base=(link.base or {}).get(k,''),platform=data[k],odoo=right[k]) for k in FIELDS if right[k]!=(link.base or {}).get(k,'')}})
                return 'scope_review'
            if dry_run:return 'would_refresh_scope'
            locked(partner).with_context(**QUIET).write(dict(local_patch(data),pe_owner_id=owner.id,
                pe_visibility=source['visibility'],pe_opt_out=bool(source.get('opt_out'))))
            link.write(dict(base=data,source_scope=source_scope,source_version=source['version'],
                owner_id=owner.id,visibility=source['visibility'],conflicts=False,status='synchronized'))
            return 'scope_refreshed'
        plan=reconcile(link.base,data,right)
        if source.get('owner')!=user.pe_user_id and right!=link.base:
            return 'awaiting_owner'
        if dry_run:
            return 'conflict' if plan['conflicts'] else ('would_update' if plan['to_platform'] or plan['to_odoo'] else 'unchanged')
        signature=local_signature(partner,link.base)
        if plan['to_platform']:
            payload=dict(operation_id=uuid.uuid4().hex,contact_id=source['id'],expected_version=source['version'],
                external_id=None,visibility=source['visibility'],fields=dict(data,**plan['to_platform']))
            operation=self.env['pe.bridge.operation'].sudo().create(dict(user_id=user.id,partner_id=partner.id,
                link_id=link.id,kind='contact',payload=payload,plan=plan,principal=proof_for(user),local_version=signature))
            operation._persist()
            operation._submit()
            if operation.state=='unknown':return 'awaiting_original_outcomes'
        else:
            locked(partner)
            if local_signature(partner,link.base)!=signature:return 'local_changed'
            if plan['to_odoo']:partner.with_context(**QUIET).write(local_patch(dict(right,**plan['to_odoo'])))
            link.write(dict(base=merged_baseline(plan,odoo_confirmed=True),source_version=source['version'],
                conflicts=plan['conflicts'],status='conflict' if plan['conflicts'] else 'synchronized',
                owner_id=owner.id))
            # A newly enabled owner can take over an existing read-only org copy.
            if partner.pe_owner_id!=owner:partner.with_context(**QUIET).write({'pe_owner_id':owner.id})
        return 'conflict' if plan['conflicts'] else 'reconciled'

    @api.model
    def _profile(self,user,*,dry_run=False,prefetched=None):
        bridge=self.env['pe.bridge.config']
        result=prefetched or bridge._call('/profile',grant=user.pe_contacts_grant,method='GET')
        validate_proof(user,result.get('principal'))
        profile=result.get('profile',{});left=dict(profile.get('fields',{}),mobile_phone='',company='')
        left=values(left)
        base=user.pe_profile_base
        if self.env['pe.bridge.operation'].sudo().search_count([('user_id','=',user.id),('kind','=','profile'),('state','=','review')]):
            return 'profile_operation_review'
        if not base:
            if not dry_run:user.write({'pe_profile_review':{'identity':{'base':'No agreed baseline','platform':'Manual profile review','odoo':'No automatic overwrite'}}})
            return 'profile_review'
        partner=user.partner_id.sudo();right=local_values(partner,base,profile=True)
        plan=reconcile(base,left,right)
        # Verified identity/channels remain authoritative in Platform. An Odoo
        # email/phone edit is a review request, never a verification bypass.
        for field in ('email','phone','mobile_phone','company'):
            if field in plan['to_platform']:
                plan['conflicts'][field]=dict(base=base[field],platform=left[field],odoo=right[field])
                del plan['to_platform'][field]
        if dry_run:return 'profile_conflict' if plan['conflicts'] else 'profile_ready'
        signature=local_signature(partner,base,profile=True)
        if plan['to_platform']:
            payload=dict(kind='profile',operation_id=uuid.uuid4().hex,expected_version=profile['version'],
                fields={k:dict(left,**plan['to_platform'])[k] for k in ('first_name','last_name')})
            operation=self.env['pe.bridge.operation'].sudo().create(dict(user_id=user.id,partner_id=partner.id,
                kind='profile',payload=payload,plan=plan,principal=proof_for(user),local_version=signature))
            operation._persist();operation._submit()
            if operation.state=='unknown':return 'awaiting_original_outcomes'
        else:
            locked(partner)
            if local_signature(partner,base,profile=True)!=signature:return 'local_changed'
            if plan['to_odoo']:partner.with_context(**QUIET).write(local_patch(dict(right,**plan['to_odoo']),profile=True))
            user.write({'pe_profile_base':merged_baseline(plan,odoo_confirmed=True),'pe_profile_review':plan['conflicts']})
        return 'profile_conflict' if plan['conflicts'] else 'profile_reconciled'

    @api.model
    def _sync_user(self,user,*,dry_run=False,pages=2):
        config=self.env['pe.bridge.config']._config()
        if not config.enabled or user.pe_org_id!=config.org_id:raise AccessDenied('Wrong enrolled organization')
        if not dry_run and not config.writes_enabled:raise AccessDenied('Synchronization is in preview mode')
        counts={}
        def count(name):counts[name]=counts.get(name,0)+1
        # First request proves the present epoch/flag and may not fall back to
        # another user's grant when a connection is unavailable.
        profile=self.env['pe.bridge.config']._call('/profile',grant=user.pe_contacts_grant,method='GET')
        validate_proof(user,profile.get('principal'))
        scope_response=self.env['pe.bridge.config']._call('/scope',grant=user.pe_contacts_grant,method='GET')
        scope=self.env['pe.bridge.config']._identity(scope_response)
        validate_proof(user,{k:scope[k] for k in ('org','user','epoch','access_version','sub')})
        if not dry_run:
            # Explicit current authorization, not absence from an incomplete page.
            removed=self.sudo().search([('org_id','=',user.pe_org_id),('owner_id','=',user.id),
                ('contact_key','not in',[str(v) for v in scope['read_contact_ids']])])
            removed.write({'status':'source_paused'})
        pending=self.env['pe.bridge.operation']
        if not dry_run:
            pending=self.env['pe.bridge.operation'].sudo().search([('user_id','=',user.id),('state','in',['prepared','unknown'])],order='id',limit=10)
            for operation in pending:operation._recover()
            if pending.filtered(lambda op:op.state in ('prepared','unknown')):
                return dict(status='awaiting_original_outcomes',counts=counts)
        try:outcome=self._profile(user,dry_run=dry_run,prefetched=None if pending else profile)
        except ValueError:outcome='profile_field_review'
        count(outcome)
        if outcome=='awaiting_original_outcomes':return dict(status=outcome,counts=counts)
        after=0 if dry_run else user.pe_scan_after
        for _ in range(min(max(pages,1),100)):
            batch=self.env['pe.bridge.config']._call('/contacts',grant=user.pe_contacts_grant,
                                                    method='GET',params=dict(after=after,limit=100))
            validate_proof(user,batch.get('principal'))
            records=batch.get('contacts')
            if not isinstance(records,list) or len(records)>100:raise AccessDenied('Invalid contact page')
            for source in records:
                try:self._validate_source(user,source)
                except ValueError:
                    count('invalid_fields')
                    continue
                outcome=self._consume(user,source,dry_run=dry_run);count(outcome)
                if outcome=='awaiting_original_outcomes':return dict(status=outcome,counts=counts)
            next_after=batch.get('next_after')
            if next_after is None:
                after=0;break
            if type(next_after) is not int or next_after<=after:raise AccessDenied('Invalid contact cursor')
            after=next_after
        if not dry_run:
            # New contacts created by linked users are private, explicit owned
            # records. Local break-glass/legacy records are never auto-exported.
            fresh=self.env['res.partner'].sudo().search([('pe_managed','=',True),('pe_org_id','=',user.pe_org_id),
                ('pe_owner_id','=',user.id),('pe_contact_key','=',False),('pe_is_profile','=',False)],limit=50)
            for partner in fresh:
                if self.env['pe.bridge.operation'].sudo().search_count([('partner_id','=',partner.id),('state','in',['prepared','unknown','review'])]):continue
                data=local_values(partner)
                plan=dict(agreed=data,to_platform=data,to_odoo={},conflicts={})
                payload=dict(operation_id=uuid.uuid4().hex,contact_id=None,expected_version=None,
                    external_id='partner:'+str(partner.id),visibility='private',fields=data)
                operation=self.env['pe.bridge.operation'].sudo().create(dict(user_id=user.id,partner_id=partner.id,
                    kind='contact',payload=payload,plan=plan,principal=proof_for(user),local_version=local_signature(partner)))
                operation._persist();operation._submit();count('new_local_contact_processed')
                if operation.state=='unknown':return dict(status='awaiting_original_outcomes',counts=counts)
            user.write({'pe_scan_after':after})
        return dict(status='preview' if dry_run else 'processed',counts=counts,next_after=after)

    @api.model
    def _sync_tick(self):
        config=self.env['pe.bridge.config']._config()
        if not config.enabled:return {'status':'disabled'}
        users=self.env['res.users'].sudo().search([('pe_org_id','=',config.org_id),('pe_subject','!=',False),
            ('pe_contacts_grant','!=',False),('active','=',True),('pe_next_sync_at','<=',fields.Datetime.now())],
            order='pe_next_sync_at,id',limit=3)
        for user in users:
            uid=user.id
            try:result=self._sync_user(user,dry_run=not config.writes_enabled)
            except AccessDenied:result=dict(status='authorization_required')
            except (ValueError,UserError):result=dict(status='review_required')
            except Exception:
                self.env.cr.rollback();self.env.invalidate_all(flush=False)
                result=dict(status='retry_required')
            user=self.env['res.users'].sudo().browse(uid)
            user.write({'pe_sync_result':result,'pe_next_sync_at':fields.Datetime.now()+timedelta(minutes=1)})
        return dict(status='processed',users=len(users))


class Operation(models.Model):
    _name='pe.bridge.operation'
    _description='Durable Platform bridge operation'
    _order='id'

    user_id=fields.Many2one('res.users',required=True,ondelete='restrict',index=True)
    partner_id=fields.Many2one('res.partner',required=True,ondelete='restrict')
    link_id=fields.Many2one('pe.bridge.link',ondelete='restrict')
    kind=fields.Selection([('contact','Contact'),('profile','Profile')],required=True)
    payload=fields.Json(required=True,groups=fields.NO_ACCESS)
    plan=fields.Json(required=True,groups=fields.NO_ACCESS)
    principal=fields.Json(required=True,groups=fields.NO_ACCESS)
    local_version=fields.Char(required=True)
    state=fields.Selection([(s,s.title()) for s in ('prepared','unknown','confirmed','review','cancelled')],default='prepared',required=True,index=True)
    attempts=fields.Integer(default=0)
    result=fields.Json(groups=fields.NO_ACCESS)
    operation_key=fields.Char(required=True,index=True,readonly=True)
    _unique_operation=models.Constraint('unique(user_id,operation_key)','An original operation ID cannot be reused')

    @api.model_create_multi
    def create(self,vals_list):
        prepared=[]
        for vals in vals_list:
            vals=dict(vals)
            key=(vals.get('payload') or {}).get('operation_id')
            if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{20,100}',key):
                raise UserError('Invalid original operation ID')
            vals['operation_key']=key
            prepared.append(vals)
        return super().create(prepared)

    def write(self,vals):
        if {'payload','principal','plan','local_version','operation_key','kind','user_id','partner_id','link_id'}.intersection(vals):
            raise AccessError('Original operation identity and approved payload are immutable')
        return super().write(vals)

    def _persist(self):
        self.env.cr.commit()

    def _finish(self,receipt):
        self.ensure_one()
        if not isinstance(receipt,dict) or not re.fullmatch('[0-9a-f]{64}',str(receipt.get('version',''))):
            raise ValueError('invalid_receipt')
        user=self.user_id.sudo();partner=locked(self.partner_id.sudo())
        base=user.pe_profile_base if self.kind=='profile' else (self.link_id.base if self.link_id else None)
        unchanged=local_signature(partner,base,profile=self.kind=='profile')==self.local_version
        plan=self.plan
        if self.kind=='profile':
            if receipt.get('user_id')!=user.pe_user_id:raise ValueError('wrong_receipt_identity')
            if unchanged and plan['to_odoo']:
                partner.with_context(**QUIET).write(local_patch(dict(local_values(partner,base,profile=True),**plan['to_odoo']),profile=True))
            user.write({'pe_profile_base':merged_baseline(plan,platform_confirmed=True,odoo_confirmed=unchanged),
                        'pe_profile_review':plan['conflicts']})
        else:
            key=receipt.get('contact_id')
            if type(key) is not int or key<=0 or (self.payload['contact_id'] is not None and self.payload['contact_id']!=key):
                raise ValueError('wrong_receipt_identity')
            if self.link_id:
                if unchanged and plan['to_odoo']:
                    partner.with_context(**QUIET).write(local_patch(dict(local_values(partner,base),**plan['to_odoo'])))
                self.link_id.write(dict(base=merged_baseline(plan,platform_confirmed=True,odoo_confirmed=unchanged),
                    source_version=receipt['version'],conflicts=plan['conflicts'],status='conflict' if plan['conflicts'] else 'synchronized'))
            else:
                existing=self.env['pe.bridge.link'].sudo().search([('org_id','=',user.pe_org_id),('contact_key','=',str(key))],limit=1)
                if existing and existing.partner_id!=partner:raise ValueError('binding_review_required')
                partner.with_context(**QUIET).write({'pe_contact_key':str(key)})
                if not existing:self.env['pe.bridge.link'].sudo().create(dict(name=partner.name,org_id=user.pe_org_id,
                    contact_key=str(key),partner_id=partner.id,owner_id=user.id,visibility='private',
                    source_scope=dict(org=user.pe_org_id,owner=user.pe_user_id,visibility='private'),base=self.payload['fields'],
                    source_version=receipt['version'],status='synchronized'))
        self.write({'state':'confirmed','result':receipt})
        self._persist()

    def _submit(self):
        self.ensure_one()
        partner=locked(self.partner_id.sudo());user=self.user_id.sudo()
        if not partner.active:
            self.write({'state':'review' if self.attempts else 'cancelled'});self._persist();return
        if self.principal!=proof_for(user):
            self.write({'state':'review'});self._persist();return
        base=user.pe_profile_base if self.kind=='profile' else (self.link_id.base if self.link_id else None)
        if not self.attempts and local_signature(partner,base,profile=self.kind=='profile')!=self.local_version:
            self.write({'state':'cancelled'});self._persist();return
        self.write({'state':'unknown','attempts':self.attempts+1})
        self._persist()
        locked(partner)
        # The original ID/body is already durable before external transfer.
        # Keep the local row locked during the transfer; never manufacture a new
        # operation ID after a timeout or mutate its approved body on retry.
        endpoint='/profile/apply' if self.kind=='profile' else '/contacts/apply'
        try:
            receipt=self.env['pe.bridge.config']._call(endpoint,grant=user.pe_contacts_grant,data=self.payload)
            self._finish(receipt)
        except BridgeRejected as error:
            state='cancelled' if error.code in ('contact_version_changed','profile_version_changed','sync_writes_not_enabled') else 'review'
            self.write({'state':state,'result':{'error':error.code}});self._persist()
        except AccessDenied:
            self.write({'state':'review' if self.attempts>=3 else 'unknown'});self._persist()

    def _recover(self):
        self.ensure_one()
        user=self.user_id.sudo()
        receipt=self.env['pe.bridge.config']._call('/effects/'+self.payload['operation_id'],
            grant=user.pe_contacts_grant,missing_ok=True)
        if receipt:
            if receipt.get('payload_sha256')!=digest(self.payload):
                self.write({'state':'review'});self._persist();return
            self._finish(receipt.get('result'))
        elif self.principal!=proof_for(user):
            self.write({'state':'review'});self._persist()
        else:self._submit()
