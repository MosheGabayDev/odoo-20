"""Revalidate Platform-linked websocket data frames, including unsolicited bus data.

Odoo20 has a model hook for incoming events but no outbound-frame model hook.
This bounded adapter wraps only the pinned bus frame sender, skips ordinary
sessions/control frames, and closes a linked connection before denied data send.
"""
from odoo.exceptions import AccessDenied
from odoo.http.session import check, session_store, SessionExpiredException
from odoo.addons.bus.websocket import Websocket
from odoo.addons.bus.websocket_protocol import Opcode, CloseCode
from odoo.addons.bus.tools.misc import acquire_cursor
from odoo.addons.bus.session_helpers import new_env


def authenticate_scope(env,session):
    user=env.user
    if not user or not user.sudo().pe_subject:return None
    bridge=env['pe.bridge.config']
    identity=bridge._identity(bridge._call('/introspect',grant=session.get('pe_login_grant')))
    if (identity['sub']!=user.sudo().pe_subject or identity['access_version']!=user.sudo().pe_access_version
            or identity['epoch']!=user.sudo().pe_native_epoch):
        raise AccessDenied('Platform authorization changed')
    return dict(identity,odoo_uid=user.id)


def authorize_socket(websocket):
    session=session_store().get(websocket._session.sid)
    for _ in range(4):
        if not session.get('next_sid'):break
        session=session_store().get(session['next_sid'])
    else:raise SessionExpiredException('Session rotation unavailable')
    if session.uid!=websocket._session.uid or not session.get('pe_login_grant'):
        raise SessionExpiredException('Session ended')
    with acquire_cursor(websocket._db) as cursor:
        env=new_env(cursor,session)
        check(session,env)
        authenticate_scope(env,session)
    websocket._session=session


def guarded_send(original,websocket,frame):
    if (frame.opcode in (Opcode.TEXT,Opcode.BINARY)
            and websocket._session.get('pe_login_grant')):
        try:authorize_socket(websocket)
        except Exception:
            # Store/network/identity failures never forward the sensitive frame.
            websocket.close(CloseCode.SESSION_EXPIRED)
            return None
    return original(websocket,frame)


def install():
    original=Websocket._send_frame
    if getattr(original,'_pe_bridge_guarded',False):return
    if original.__module__!='odoo.addons.bus.websocket':
        raise RuntimeError('Review the existing websocket sender extension before enabling the bridge')
    def send(websocket,frame):return guarded_send(original,websocket,frame)
    send._pe_bridge_guarded=True
    Websocket._send_frame=send
