from odoo import http
from odoo.http import request,Response
from odoo.addons.platform_mcp_guard.controllers import ScopedMCP
from odoo.addons.muk_mcp.controllers.mcp import MCPController


class OrganizationMCP(ScopedMCP):
    @http.route(cors=None)
    def mcp(self,**kwargs):
        cfg=request.env['pe.mcp.config']._config()
        if not cfg or cfg.access_mode!='organization':return super().mcp(**kwargs)
        if request.httprequest.content_length and request.httprequest.content_length>8*1024*1024:return Response(status=413)
        result=MCPController.mcp(self,**kwargs)
        result.headers['Cache-Control']='no-store';result.headers['X-Content-Type-Options']='nosniff'
        return result
