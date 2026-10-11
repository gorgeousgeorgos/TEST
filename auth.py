"""One-time Google OAuth 2.0 (PKCE, loopback) helper. Stores a refresh token locally.

    python auth.py
"""
import base64
import hashlib
import http.server
import secrets
import socketserver
import threading
import time
import urllib.parse
import webbrowser

import httpx

from vitals import config
from vitals.google_health import TOKEN_URL, save_token

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
PORT = 8765


def main() -> None:
    cfg = config.load()
    if not cfg.google_client_id or not cfg.google_client_secret:
        raise SystemExit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first.")

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    redirect = f"http://127.0.0.1:{PORT}"
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = query.get("code", [None])[0]
            result["state"] = query.get("state", [None])[0]
            result["error"] = query.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and return to the terminal.")

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cfg.google_client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(cfg.google_scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    print("Opening browser. If it does not open, visit:\n" + url + "\n")
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()

    if result.get("error") or not result.get("code"):
        raise SystemExit(f"Auth failed: {result.get('error') or 'no code received'}")
    if result.get("state") != state:
        raise SystemExit("State mismatch; aborting.")

    resp = httpx.post(TOKEN_URL, data={
        "client_id": cfg.google_client_id,
        "client_secret": cfg.google_client_secret,
        "code": result["code"],
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    })
    resp.raise_for_status()
    token = resp.json()
    if "refresh_token" not in token:
        raise SystemExit("No refresh_token returned. Revoke the app at myaccount.google.com/permissions and retry.")
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    save_token(cfg, token)
    print(f"Saved token to {cfg.google_token_path}")


if __name__ == "__main__":
    main()
