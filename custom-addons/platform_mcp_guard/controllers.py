from odoo import http
from odoo.http import request,Response
from odoo.addons.muk_mcp.controllers.mcp import MCPController


class ScopedMCP(MCPController):
    def _handle_initialize(self,params):
        result=super()._handle_initialize(params)
        result['capabilities']={'tools':{}}
        return result

    def _handle_discover(self,params):
        result=super()._handle_discover(params)
        result['capabilities']={'tools':{}}
        return result

    def _get_handlers(self,protocol_version):
        allowed={'initialize','server/discover','ping','tools/list','tools/call'}
        return {k:v for k,v in super()._get_handlers(protocol_version).items() if k in allowed}

    def _check_origin(self):
        origin=request.httprequest.headers.get('Origin')
        return not origin or origin.rstrip('/')=='https://odoo.test.ai-data-platform.com'

    @http.route(cors=None)
    def mcp(self,**kwargs):
        if request.httprequest.content_length and request.httprequest.content_length>16384:
            return Response(status=413)
        response=super().mcp(**kwargs)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response
