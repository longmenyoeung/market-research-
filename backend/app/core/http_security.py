"""Local/application authentication and bounded HTTP request buffering."""
import asyncio
import base64
import secrets
from collections import OrderedDict
from time import monotonic

from starlette.responses import JSONResponse


def valid_application_key(expected: str | None, supplied: str | None) -> bool:
    return bool(expected and supplied and secrets.compare_digest(expected.encode(),supplied.encode()))


def websocket_application_key(headers) -> str | None:
    direct=headers.get('x-api-key')
    if direct: return direct
    for protocol in headers.get('sec-websocket-protocol','').split(','):
        protocol=protocol.strip()
        if protocol.startswith('mca-auth.'):
            encoded=protocol.removeprefix('mca-auth.')
            if len(encoded)>700: return None
            try:
                return base64.b64decode(encoded+'='*((-len(encoded))%4),altchars=b'-_',validate=True).decode()
            except (ValueError,UnicodeError):
                return None
    return None


class RequestBodyLimitMiddleware:
    def __init__(self,app,max_bytes=524288):
        self.app,self.max_bytes=app,max_bytes

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in {'POST','PUT','PATCH','DELETE'}:
            return await self.app(scope,receive,send)
        chunks=[];size=0;deadline=monotonic()+10
        while True:
            try:
                event=await asyncio.wait_for(receive(),max(.01,deadline-monotonic()))
            except TimeoutError:
                return await JSONResponse({'detail':'Request body timed out.'},status_code=408,headers={'Cache-Control':'no-store'})(scope,receive,send)
            if event['type']=='http.disconnect': return
            body=event.get('body',b'');size+=len(body)
            if size>self.max_bytes:
                return await JSONResponse({'detail':'Request body exceeds the configured limit.'},status_code=413,headers={'Cache-Control':'no-store'})(scope,receive,send)
            chunks.append(body)
            if not event.get('more_body',False): break
        pending=True
        async def replay():
            nonlocal pending
            if pending:
                pending=False
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)


class AuthenticationFailures:
    """Bounded local throttle; correct keys are never blocked by failed attempts."""
    def __init__(self):
        self.failures=OrderedDict()

    def reject_status(self,host):
        now=monotonic()
        attempts=[t for t in self.failures.pop(host,[]) if now-t<60]
        attempts.append(now)
        self.failures[host]=attempts[-21:]
        while len(self.failures)>4096: self.failures.popitem(last=False)
        return 429 if len(attempts)>20 else 401
