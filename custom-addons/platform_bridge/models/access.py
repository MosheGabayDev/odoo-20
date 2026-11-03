"""Odoo20 ir.access restrictions, including fresh native policy in cache keys."""
import hashlib
import json

from odoo import models
from odoo.http import request
from odoo.http.session import SessionExpiredException, logout
from odoo.exceptions import AccessDenied
from .bridge import request_scope
from ..socket_guard import authenticate_scope,install


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def _register_hook(self):
        result=super()._register_hook()
        install()
        return result

    @classmethod
    def _authenticate(cls, endpoint):
        super()._authenticate(endpoint)
        try:
            identity=authenticate_scope(request.env,request.session)
        except AccessDenied:
            logout(request.session,keep_db=True)
            raise SessionExpiredException('Platform authorization ended') from None
        # A browser-supplied RPC context cannot replace this request attribute.
        if identity:request._pe_bridge_scope=identity


class IrWebsocket(models.AbstractModel):
    _inherit='ir.websocket'

    @classmethod
    def _authenticate(cls):
        super()._authenticate()
        from odoo.addons.bus.websocket import wsrequest
        try:
            identity=authenticate_scope(wsrequest.env,wsrequest.session)
        except AccessDenied:
            raise SessionExpiredException('Platform authorization ended') from None
        if identity:wsrequest._pe_bridge_scope=identity


class IrAccess(models.Model):
    _inherit = 'ir.access'

    def _get_access_context(self):
        yield from super()._get_access_context()
        scope=request_scope()
        if scope and scope.get('odoo_uid')==self.env.uid:
            yield hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()
        else:
            yield 'no-platform-request-scope'
