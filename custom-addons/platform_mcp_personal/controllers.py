"""Human PKCE consent callback and fresh authority at file publication."""
import base64,hashlib,secrets,time
from urllib.parse import urlencode
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from odoo import fields,http
from odoo.exceptions import AccessDenied,AccessError,ValidationError
from odoo.http import request,Response
from werkzeug.exceptions import NotFound
from odoo.addons.platform_bridge.models.bridge import ORIGIN,CLIENT,TOKEN
from odoo.addons.muk_mcp.controllers.transfer import MCPTransferController
from odoo.addons.platform_mcp_organization.models import config
from odoo.addons.platform_mcp_organization.policy import READ,WRITE
from .models import PURPOSE

CALLBACK='https://odoo.test.ai-data-platform.com/platform/mcp/signin'


def denied():
    return Response('החיבור האישי לא הושלם. חזור לפרופיל ב־Platform Engineer ופתח שוב את Odoo. אין לשלוח סיסמה או קוד.',status=403,content_type='text/plain; charset=utf-8',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


class PersonalConsent(http.Controller):
    @http.route('/platform/mcp/connect',type='http',auth='user',methods=['GET'])
    def connect(self,**kwargs):
        cfg=config(request.env).sudo();user=request.env.user
        if not cfg or not cfg._personal_target_ready() or user!=cfg.personal_user_id:return denied()
        user._pe_identity()
        try:
            public=serialization.load_pem_public_key(cfg.personal_transport_public_pem.encode())
            if not isinstance(public,rsa.RSAPublicKey) or public.key_size<3072:raise ValueError()
        except (ValueError,AttributeError,TypeError):return denied()
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(32)
        request.session['personal_mcp_intent']=dict(state=state,verifier=verifier,uid=user.id,at=time.time(),transport=hashlib.sha256(cfg.personal_transport_public_pem.encode()).hexdigest())
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        response=request.redirect(ORIGIN+'/mcp/authorize?'+urlencode(dict(client_id=CLIENT,response_type='code',redirect_uri=CALLBACK,state=state,code_challenge=challenge,code_challenge_method='S256')),303,local=False)
        response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer';return response

    @http.route('/platform/mcp/signin',type='http',auth='user',methods=['GET'])
    def signin(self,code=None,state=None,**kwargs):
        cfg=config(request.env).sudo();intent=request.session.pop('personal_mcp_intent',None);grant=None
        if (not cfg or not isinstance(intent,dict) or not isinstance(state,str) or not TOKEN.fullmatch(state)
            or not secrets.compare_digest(intent.get('state',''),state) or not isinstance(code,str) or not TOKEN.fullmatch(code)
            or not 0<=time.time()-intent.get('at',0)<=300 or intent.get('uid')!=request.env.uid
            or not cfg._personal_target_ready() or request.env.user!=cfg.personal_user_id
            or intent.get('transport')!=hashlib.sha256((cfg.personal_transport_public_pem or '').encode()).hexdigest()):return denied()
        try:
            user=request.env.user;user._pe_identity()
            bridge=request.env['pe.bridge.config']
            result=bridge._call('/mcp/token',data=dict(grant_type='authorization_code',code=code,code_verifier=intent['verifier'],redirect_uri=CALLBACK))
            identity=bridge._identity(result);grant=result.get('mcp_grant')
            if (not isinstance(grant,str) or not TOKEN.fullmatch(grant) or result.get('purpose')!=PURPOSE
                or result.get('continuous') is not True or 'expires_at' not in result or result['expires_at'] is not None
                or identity['sub']!=user.sudo().pe_subject or identity['epoch']!=user.sudo().pe_native_epoch
                or identity['access_version']!=user.sudo().pe_access_version):raise AccessDenied()
            # The read-only pilot remains valid during preparation. Its mode
            # changes only now, after the actual human native consent exchange.
            cfg.write(dict(enabled=False,access_mode='personal',user_id=user.id,continuous=True,project_id=False,task_ids=[fields.Command.clear()]))
            cfg.enabled=True
            for model in READ:
                entry=request.env['muk_mcp_access.model'].sudo().with_context(active_test=False).search([('model_name','=',model)],limit=1)
                values=dict(active=True,allow_read=True,allow_write=model in WRITE,domain=f"user._mcp_org_domain('{model}')")
                if entry:entry.write(values)
                else:request.env['muk_mcp_access.model'].sudo().create(dict(values,model_id=request.env['ir.model']._get(model).id))
            request._personal_mcp_consent=dict(grant=grant)
            key,raw=request.env['muk_mcp.key'].with_user(user)._generate('Codex personal continuous business access','write',120)
            public=serialization.load_pem_public_key(cfg.personal_transport_public_pem.encode())
            if not isinstance(public,rsa.RSAPublicKey) or public.key_size<3072:raise AccessDenied()
            cipher=public.encrypt(raw.encode(),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
            key.sudo().write({'personal_delivery_ciphertext':base64.b64encode(cipher)})
            request.env['muk_mcp.key'].sudo().with_context(active_test=False).search([('id','!=',key.id),('active','=',True)]).write({'active':False})
            cfg._monitor_security();request.env.cr.commit()
        except Exception:
            request.env.cr.rollback()
            if grant:
                try:request.env['pe.bridge.config']._call('/mcp/revoke',grant=grant)
                except AccessDenied:pass
            return denied()
        return Response('ההרשאה האישית אושרה. Codex ישלים את החיבור המוצפן במחשב שלך. אפשר לבטל אותה דרך הפרופיל ב־Platform Engineer.',content_type='text/plain; charset=utf-8',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


class PersonalTransfer(MCPTransferController):
    @http.route()
    def transfer(self,token,**kwargs):
        cfg=config(request.env)
        if not cfg or cfg.access_mode!='personal':return super().transfer(token,**kwargs)
        if request.httprequest.content_length and request.httprequest.content_length>5*1024*1024:return Response(status=413)
        try:
            with request.env.cr.savepoint():
                response=super().transfer(token,**kwargs)
                key=getattr(request,'_personal_mcp_key',None)
                if not key or key._personal_scope()!=getattr(request,'_pe_bridge_scope',None):raise AccessDenied()
                return response
        except AccessDenied:raise NotFound() from None
