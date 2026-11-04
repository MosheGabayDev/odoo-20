{
    'name':'Organization MCP access',
    'version':'20.0.1.0.0',
    'license':'LGPL-3',
    'depends':['platform_mcp_guard','sale_management','account','calendar'],
    'data':['security/access.xml','security/ir.access.csv','data/cron.xml','views/config.xml'],
    'installable':True,
    'auto_install':False,
}
