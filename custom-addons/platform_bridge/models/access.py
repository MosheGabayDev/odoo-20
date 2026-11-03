"""Odoo20 ir.access restrictions, including fresh native policy in cache keys."""
import hashlib
import json

from odoo import models
from odoo.http import request
from odoo.http.session import SessionExpiredException, logout
from odoo.exceptions import AccessDenied
from .bridge import request_scope


class IrHttp(models.Model):
    _inherit = 'ir.http'

    @classmethod
    def _authenticate(cls, endpoint):
        super()._authenticate(endpoint)
        user=request.env.user
        if not user or not user.sudo().pe_subject:
            return
        token=request.session.get('pe_login_grant')
        try:
            bridge=request.env['pe.bridge.config']
            identity=bridge._identity(bridge._call('/introspect',grant=token))
            if (identity['sub']!=user.sudo().pe_subject
                    or identity['access_version']!=user.sudo().pe_access_version):
                raise AccessDenied()
        except AccessDenied:
            logout(request.session,keep_db=True)
            raise SessionExpiredException('Platform authorization ended') from None
        # A browser-supplied RPC context cannot replace this request attribute.
        request._pe_bridge_scope=dict(identity,odoo_uid=user.id)
        # Do not allow a long-lived un-revalidated socket to outlive revocation.
        # Ordinary local-admin websocket support is unchanged.
        if request.httprequest.path.startswith('/websocket'):
            raise AccessDenied('Realtime transport is not enabled for Platform-linked accounts')


class IrAccess(models.Model):
    _inherit = 'ir.access'

    def _get_access_context(self):
        yield from super()._get_access_context()
        scope=request_scope()
        if scope and scope.get('odoo_uid')==self.env.uid:
            yield hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()
        else:
            yield 'no-platform-request-scope'
