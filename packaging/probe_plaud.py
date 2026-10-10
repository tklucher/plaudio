"""One-off check that Plaud's hosted connector will accept PlaudIO's sign-in setup.

Registers PlaudIO the way the app does (dynamic client registration with the
http://localhost:8199/callback redirect), then asks for the sign-in page without
signing in. Prints what Plaud said. Run by CI only when a commit message contains
[plaud-probe], so Plaud doesn't get a throwaway registration on every push.
"""
import base64
import hashlib
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://mcp.plaud.ai"
REDIRECT = "http://localhost:8199/callback"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main() -> int:
    meta = json.load(urllib.request.urlopen(f"{BASE}/.well-known/oauth-authorization-server", timeout=20))
    print("metadata: registration_endpoint =", meta.get("registration_endpoint"))
    body = json.dumps({"client_name": "PlaudIO (setup check)", "redirect_uris": [REDIRECT],
                       "grant_types": ["authorization_code", "refresh_token"],
                       "response_types": ["code"], "token_endpoint_auth_method": "none"}).encode()
    req = urllib.request.Request(meta["registration_endpoint"], data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        reg = json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as exc:
        print("REGISTRATION REFUSED:", exc.code, exc.read()[:500].decode(errors="replace"))
        return 1
    print("registration: ok, redirect_uris =", reg.get("redirect_uris"))

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = urllib.parse.urlencode({"response_type": "code", "client_id": reg["client_id"],
                                "redirect_uri": REDIRECT, "code_challenge": challenge,
                                "code_challenge_method": "S256", "state": "probe",
                                "resource": f"{BASE}/mcp"})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(f"{meta['authorization_endpoint']}?{q}", timeout=20)
        status, location, text = resp.status, resp.headers.get("Location"), resp.read()[:300]
    except urllib.error.HTTPError as exc:
        status, location, text = exc.code, exc.headers.get("Location"), exc.read()[:300]
    host = urllib.parse.urlparse(location).netloc if location else None
    print(f"authorize: HTTP {status}, redirects to host {host!r}")
    if location and location.startswith(REDIRECT):
        print("NOTE: Plaud sent the browser straight back to the app:",
              urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("error"))
    if status >= 400:
        print("AUTHORIZE REFUSED:", text.decode(errors="replace"))
        return 1
    print("RESULT: Plaud accepts PlaudIO's sign-in setup (sign-in page reached).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
