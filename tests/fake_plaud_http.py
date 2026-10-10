"""A stand-in for Plaud's hosted connector (https://mcp.plaud.ai/mcp) used by the tests:
the same tools as fake_plaud_mcp.py, served over HTTP behind a real MCP OAuth flow
(dynamic client registration + PKCE + refresh tokens). Authorization is auto-approved,
so a test "browser" only has to follow the redirect back to the app.

Run: python fake_plaud_http.py PORT
"""
import os
import secrets
import sys
import time

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      RefreshToken, construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

import fake_plaud_mcp  # reuse the tool implementations

try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server


class AutoApproveProvider:
    def __init__(self):
        self.clients, self.codes, self.access, self.refresh = {}, {}, {}, {}
        self.access_ttl = int(os.environ.get("FAKE_ACCESS_TTL", "3600"))

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def register_client(self, client_info):
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=time.time() + 300,
            client_id=client.client_id, code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, code):
        return self.codes.get(code)

    def _issue(self, client_id, scopes, resource):
        from mcp.shared.auth import OAuthToken
        at, rt = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.access[at] = AccessToken(token=at, client_id=client_id, scopes=scopes,
                                      expires_at=int(time.time()) + self.access_ttl,
                                      resource=resource)
        self.refresh[rt] = RefreshToken(token=rt, client_id=client_id, scopes=scopes,
                                        resource=resource)
        return OAuthToken(access_token=at, token_type="Bearer", expires_in=self.access_ttl,
                          refresh_token=rt, scope=" ".join(scopes) or None)

    async def exchange_authorization_code(self, client, authorization_code):
        self.codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token):
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        self.refresh.pop(refresh_token.token, None)
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token):
        at = self.access.get(token)
        if at and at.expires_at and at.expires_at < time.time():
            return None
        return at

    async def revoke_token(self, token):
        self.access.pop(getattr(token, "token", ""), None)
        self.refresh.pop(getattr(token, "token", ""), None)


def build(port: int):
    base = f"http://127.0.0.1:{port}"
    server = _Server("fake-plaud-http", auth_server_provider=AutoApproveProvider(),
                     auth=AuthSettings(issuer_url=base, resource_server_url=f"{base}/mcp",
                                       client_registration_options=ClientRegistrationOptions(enabled=True),
                                       required_scopes=[], validate_token_resource=True))
    for name in ("get_current_user", "list_files", "get_note", "get_transcript"):
        server.tool()(getattr(fake_plaud_mcp, name))
    return server


if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1])
    uvicorn.run(build(port).streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")
