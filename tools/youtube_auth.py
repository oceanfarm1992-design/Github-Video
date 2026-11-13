"""One-time YouTube authorization (loopback OAuth for a Desktop client).

    python tools/youtube_auth.py [client_secret.json] [--channel kids|everyday]

--channel stores the token (and the OAuth client it was issued to) for a sister channel
(YOUTUBE_REFRESH_TOKEN_KIDS / _EVERYDAY, YOUTUBE_CLIENT_ID_KIDS ...): on Google's screen, pick that channel.

Opens your browser, you approve the `youtube.upload`, `youtube.readonly` and `youtube.force-ssl` scopes, and the
refresh token is stored straight into GitHub Actions secrets with the `gh` CLI. Tokens are
never printed or written to disk. Pass --print only if you must handle the token yourself.
"""
import http.server
import json
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = ("https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly "
          "https://www.googleapis.com/auth/youtube.force-ssl")  # force-ssl: reply to comments


def main():
    args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] != "--channel"]
    path = args[0] if args else "client_secret.json"
    cfg = json.load(open(path))["installed"]
    state = secrets.token_urlsafe(16)
    result = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"], result["state"], result["error"] = (q.get(k, [None])[0] for k in ("code", "state", "error"))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authorization received. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = cfg["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening browser for Google consent...")
    webbrowser.open(url)
    t = threading.Thread(target=srv.handle_request)
    t.start()
    t.join(timeout=300)
    if result.get("error") or not result.get("code") or result.get("state") != state:
        sys.exit(f"Authorization failed: {result.get('error') or 'no code / state mismatch / timeout'}")
    body = urllib.parse.urlencode({
        "code": result["code"], "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    tok = json.load(urllib.request.urlopen(urllib.request.Request(cfg["token_uri"], data=body)))
    rt = tok.get("refresh_token")
    if not rt:
        sys.exit("No refresh token returned. Revoke the app at myaccount.google.com/permissions and retry.")
    if "--print" in sys.argv:
        print(rt)
        return
    channel = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--channel=")), None) or         (sys.argv[sys.argv.index("--channel") + 1] if "--channel" in sys.argv[:-1] else None)
    if channel:
        if channel not in ("kids", "everyday"):
            sys.exit("--channel must be kids or everyday")
        up = channel.upper()  # the channel's own OAuth client too: a token only works with the client that issued it
        pairs = ((f"YOUTUBE_CLIENT_ID_{up}", cfg["client_id"]), (f"YOUTUBE_CLIENT_SECRET_{up}", cfg["client_secret"]),
                 (f"YOUTUBE_REFRESH_TOKEN_{up}", rt))
    else:
        pairs = (("YOUTUBE_CLIENT_ID", cfg["client_id"]), ("YOUTUBE_CLIENT_SECRET", cfg["client_secret"]),
                 ("YOUTUBE_REFRESH_TOKEN", rt))
    for name, val in pairs:
        # value via stdin, never argv (argv is visible to other processes)
        subprocess.run(["gh", "secret", "set", name], input=val, text=True, check=True, capture_output=True)
        print("set secret", name)


if __name__ == "__main__":
    main()
