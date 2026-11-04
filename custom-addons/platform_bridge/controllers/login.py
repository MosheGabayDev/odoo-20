"""Fixed authorization-code callback. Browser state/verifier stay in Odoo session."""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

from odoo.exceptions import AccessDenied
from odoo.http import Controller, request, route
from odoo.http.session import authenticate
from odoo.addons.web.controllers.utils import ensure_db, _get_login_redirect_url

from ..models.bridge import CLIENT, ORIGIN, CALLBACK, TOKEN


class Login(Controller):
    @route('/platform/login',type='http',auth='none',methods=['GET'],readonly=False)
    def start(self,**kwargs):
        ensure_db()
        config=request.env['pe.bridge.config']._config()
        if not config.enabled or config.org_id<=0:
            return request.redirect('/web/login?pe_error=unavailable',303)
        state,verifier=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        # A single reviewed product destination, never an arbitrary return URL.
        request.session['pe_login_intent']=dict(state=state,verifier=verifier,at=time.time(),personal_mcp=kwargs.get('purpose')=='mcp')
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        response=request.redirect(ORIGIN+'/authorize?'+urlencode(dict(client_id=CLIENT,
            response_type='code',redirect_uri=CALLBACK,state=state,
            code_challenge=challenge,code_challenge_method='S256')),303,local=False)
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        return response

    @route('/platform/signin',type='http',auth='none',methods=['GET'],readonly=False)
    def signin(self,code=None,state=None,**kwargs):
        ensure_db()
        intent=request.session.pop('pe_login_intent',None)
        if (not isinstance(intent,dict) or not isinstance(state,str) or not TOKEN.fullmatch(state)
                or not secrets.compare_digest(intent.get('state',''),state)
                or not isinstance(code,str) or not TOKEN.fullmatch(code)
                or not 0<=time.time()-intent.get('at',0)<=300):
            return request.redirect('/web/login?pe_error=invalid_login',303)
        bridge=request.env['pe.bridge.config']
        try:
            response=bridge._call('/token',data=dict(grant_type='authorization_code',
                code=code,code_verifier=intent['verifier'],redirect_uri=CALLBACK))
            identity=bridge._identity(response)
            login_grant=response.get('login_grant')
            if not isinstance(login_grant,str) or not TOKEN.fullmatch(login_grant):
                raise AccessDenied()
            user=bridge._provision(identity,response.get('contacts_grant'))
            # Commit identity provisioning before native authenticate opens its
            # independent login cursor. No business contact synchronization here.
            request.env.cr.commit()
            authenticate(request.session,request.env,dict(type='pe_grant',login=user.login,grant=login_grant))
            request.session['pe_login_grant']=login_grant
        except AccessDenied:
            return request.redirect('/web/login?pe_error=access',303)
        destination='/platform/mcp/connect' if intent.get('personal_mcp') is True else '/odoo/contacts'
        result=request.redirect(_get_login_redirect_url(user.id,destination),303)
        result.headers['Cache-Control']='no-store'
        result.headers['Referrer-Policy']='no-referrer'
        return result
