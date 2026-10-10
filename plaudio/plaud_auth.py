"""Browser sign-in for Plaud's hosted MCP server (https://mcp.plaud.ai/mcp).

Standard MCP OAuth: the app registers itself with Plaud (dynamic client
registration), opens the Plaud sign-in page in the browser, and a tiny web
server on this PC catches the redirect when the user clicks Authorize.
Tokens are saved in ~/.plaudio/plaud_oauth.json and refreshed automatically,
so the browser only opens the first time (or if Plaud revokes access).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (AuthorizationCodeResult, OAuthClientInformationFull,
                             OAuthClientMetadata, OAuthToken)

DEFAULT_REDIRECT_PORT = 8199          # same callback port Plaud's own connector uses
SIGN_IN_TIMEOUT = 300                 # seconds to wait for the user to finish in the browser

_DONE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>PlaudIO</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;text-align:center;padding-top:80px">
<h2>{title}</h2><p>{text}</p></body></html>"""


class PlaudSignInError(RuntimeError):
    pass


class FileTokenStorage(TokenStorage):
    """Keeps Plaud tokens and this app's registration in one small JSON file."""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        # Remember when the access token runs out, so the next app start renews it
        # quietly with the refresh token instead of asking to sign in again.
        data["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in else None
        self._write(data)

    def saved_expiry(self) -> float | None:
        exp = self._read().get("expires_at")
        return float(exp) - 60 if exp else None  # renew a minute early

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


class _CallbackCatcher:
    """One-shot local web server that receives ?code=...&state=... from the browser."""

    def __init__(self, port: int):
        self.port = port
        self.result: dict[str, str] | None = None
        self._got = threading.Event()
        self._server: HTTPServer | None = None

    def start(self) -> None:
        catcher = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                if "code" not in query and "error" not in query:
                    self.send_response(404)
                    self.end_headers()
                    return
                ok = "code" in query
                page = _DONE_PAGE.format(
                    title="Signed in to Plaud" if ok else "Plaud sign-in didn't finish",
                    text="You can close this tab and go back to PlaudIO." if ok
                    else query.get("error_description") or query.get("error", ""))
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                catcher.result = query
                catcher._got.set()

            def log_message(self, *args):  # keep quiet
                pass

        try:
            self._server = HTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as exc:
            raise PlaudSignInError(
                f"Couldn't start the Plaud sign-in helper on port {self.port} ({exc}). "
                "Close other apps that might be signing in to Plaud and try again.") from exc
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def wait(self, timeout: float) -> dict[str, str]:
        try:
            if not self._got.wait(timeout):
                raise PlaudSignInError("Plaud sign-in timed out. Press the button to try again.")
            return self.result or {}
        finally:
            self.stop()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class _PlaudOAuth(OAuthClientProvider):
    """The SDK's provider forgets token expiry across restarts; restore it from the file."""

    def __init__(self, *args, storage: FileTokenStorage, **kwargs):
        super().__init__(*args, storage=storage, **kwargs)
        self._file_storage = storage

    async def _initialize(self) -> None:
        await super()._initialize()
        expiry = self._file_storage.saved_expiry()
        if expiry and self.context.current_tokens:
            self.context.token_expiry_time = expiry


def make_oauth(server_url: str, token_path: Path, *, port: int = DEFAULT_REDIRECT_PORT,
               open_browser: Callable[[str], object] = webbrowser.open,
               timeout: float = SIGN_IN_TIMEOUT) -> OAuthClientProvider:
    """An httpx auth handler that signs in to Plaud when needed."""
    redirect_uri = f"http://localhost:{port}/callback"
    catcher: dict[str, _CallbackCatcher] = {}

    async def redirect_handler(authorization_url: str) -> None:
        c = _CallbackCatcher(port)
        c.start()                      # listen before the browser can come back
        catcher["c"] = c
        await asyncio.to_thread(open_browser, authorization_url)

    async def callback_handler() -> AuthorizationCodeResult:
        c = catcher.pop("c")
        query = await asyncio.to_thread(c.wait, timeout)
        if "code" not in query:
            raise PlaudSignInError("Plaud sign-in was cancelled or refused: "
                                   + (query.get("error_description") or query.get("error", "unknown")))
        return AuthorizationCodeResult(code=query["code"], state=query.get("state"),
                                       iss=query.get("iss"))

    metadata = OAuthClientMetadata(
        client_name="PlaudIO",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    return _PlaudOAuth(server_url=server_url, client_metadata=metadata,
                               storage=FileTokenStorage(token_path),
                               redirect_handler=redirect_handler,
                               callback_handler=callback_handler)
