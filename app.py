"""iPhoneCam per Windows v2 — server HTTPS locale + signaling WebRTC + controlli remoti.

Zero app su iOS: l'iPhone apre Safari via QR e invia il video con WebRTC.
Il PC riceve nel browser (dashboard) e puo' registrare, scattare foto,
cambiare camera/torcia/qualita' da remoto via coda comandi.
"""
import argparse
import base64
import datetime
import http.server
import json
import os
import secrets
import socket
import socketserver
import ssl
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse, parse_qs

__version__ = "2.0.0"

# ----------------------------------------------------------------------------
# Percorsi (compatibile PyInstaller --onefile: i file dati finiscono in _MEIPASS)
# ----------------------------------------------------------------------------
def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = BASE_DIR  # cert/key/snapshots vivono accanto all'exe, non nel bundle
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
os.makedirs(SNAP_DIR, exist_ok=True)

PORT = 4443
TOKEN = secrets.token_hex(4)

store = {
    "offer": None,
    "answer": None,
    "ice_phone": [],
    "ice_pc": [],
    "control": [],   # comandi PC -> iPhone, es. {"id":..,"type":"torch","on":True}
    "events": [],    # eventi iPhone -> PC, es. {"type":"pong","t":..}
    "shots": [],     # nomi file, dal piu' recente
    "pc_seen": 0.0,  # ultimo heartbeat dashboard
    "ctl_id": 0,
}


# ----------------------------------------------------------------------------
# Rete / certificato
# ----------------------------------------------------------------------------
def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = a[4][0]
            if ip.startswith("127.") or ip in ips:
                continue
            ips.append(ip)
    except Exception:
        pass
    # Tethering USB iPhone (Apple Mobile Device Ethernet) in testa
    ips.sort(key=lambda x: (not x.startswith("172.20.10."), x))
    return ips or ["127.0.0.1"]


def ensure_cert(ips):
    cert = os.path.join(DATA_DIR, "cert.pem")
    key = os.path.join(DATA_DIR, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    try:
        import ipaddress

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iPhoneCam")])
        sans = [x509.DNSName("localhost")]
        for ip in ips:
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except Exception:
                pass
        c = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(k, hashes.SHA256())
        )
        with open(key, "wb") as f:
            f.write(
                k.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
        with open(cert, "wb") as f:
            f.write(c.public_bytes(serialization.Encoding.PEM))
        print("[iPhoneCam] Certificato HTTPS generato. Su iPhone va accettato UNA volta.")
        return cert, key
    except ImportError:
        print("[iPhoneCam] 'cryptography' mancante: HTTPS disabilitato.")
        print("            Esegui: py -m pip install -r requirements.txt")
        return None, None


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------
class H(http.server.SimpleHTTPRequestHandler):
    server_version = "iPhoneCam/2.0"

    def log_message(self, *a):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), " ".join(map(str, a[1:]))))

    # -- helper ---------------------------------------------------------
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def _page(self, name):
        with open(resource_path(name), "rb") as f:
            b = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n <= 0 or n > 8 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # -- GET ------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            return self._page("dashboard.html")
        if u.path == "/phone":
            return self._page("phone.html")

        if u.path == "/api/info":
            ips = lan_ips()
            return self._json(
                {
                    "version": __version__,
                    "ips": ips,
                    "port": PORT,
                    "token": TOKEN,
                    "phoneUrl": f"https://{ips[0]}:{PORT}/phone?t={TOKEN}",
                    "pcOnline": (time.time() - store["pc_seen"]) < 12,
                }
            )
        if u.path == "/api/offer":
            return self._json({"sdp": store["offer"]})
        if u.path == "/api/answer":
            return self._json({"sdp": store["answer"]})
        if u.path == "/api/ice-phone":
            n = int(q.get("n", ["0"])[0])
            return self._json({"cands": store["ice_phone"][n:], "n": len(store["ice_phone"])})
        if u.path == "/api/ice-pc":
            n = int(q.get("n", ["0"])[0])
            return self._json({"cands": store["ice_pc"][n:], "n": len(store["ice_pc"])})
        if u.path == "/api/control":
            n = int(q.get("n", ["0"])[0])
            return self._json({"cmds": store["control"][n:], "n": len(store["control"])})
        if u.path == "/api/events":
            n = int(q.get("n", ["0"])[0])
            return self._json({"evts": store["events"][n:], "n": len(store["events"])})
        if u.path == "/api/shots":
            return self._json({"shots": store["shots"]})
        if u.path.startswith("/shots/"):
            name = os.path.basename(u.path[len("/shots/"):])
            if not name.lower().endswith((".jpg", ".jpeg")):
                return self.send_error(404)
            p = os.path.join(SNAP_DIR, name)
            if not os.path.exists(p):
                return self.send_error(404)
            with open(p, "rb") as f:
                b = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        return self.send_error(404)

    # -- POST -----------------------------------------------------------
    def do_POST(self):
        global TOKEN
        u = urlparse(self.path)
        data = self._body()

        if u.path == "/api/ping":
            store["pc_seen"] = time.time()
            return self._json({"ok": True, "n_control": len(store["control"])})
        if u.path == "/api/regen":
            TOKEN = secrets.token_hex(4)
            store.update(
                {"offer": None, "answer": None, "ice_phone": [],
                 "ice_pc": [], "control": [], "events": []}
            )
            return self._json({"ok": True, "token": TOKEN})
        if u.path == "/api/offer":
            store["offer"] = data.get("sdp")
            store["answer"] = None
            store["ice_phone"] = []
            store["ice_pc"] = []
            store["control"] = []
            store["events"] = []
            print("[iPhoneCam] Offerta ricevuta da iPhone")
            return self._json({"ok": True})
        if u.path == "/api/answer":
            store["answer"] = data.get("sdp")
            return self._json({"ok": True})
        if u.path == "/api/ice-phone":
            if data.get("c"):
                store["ice_phone"].append(data["c"])
            return self._json({"ok": True})
        if u.path == "/api/ice-pc":
            if data.get("c"):
                store["ice_pc"].append(data["c"])
            return self._json({"ok": True})
        if u.path == "/api/control":
            # PC -> iPhone (torcia, cambio camera, qualita', ping, scatto remoto)
            store["ctl_id"] += 1
            cmd = {"id": store["ctl_id"], **{k: v for k, v in data.items() if k != "c"}}
            store["control"].append(cmd)
            store["control"] = store["control"][-50:]
            return self._json({"ok": True, "id": store["ctl_id"]})
        if u.path == "/api/event":
            store["events"].append(data)
            store["events"] = store["events"][-50:]
            return self._json({"ok": True})
        if u.path == "/api/shot":
            # JPEG base64 (dataURL) dall'iPhone o dal PC -> salva in snapshots/
            raw = data.get("jpeg", "")
            if "," in raw:
                raw = raw.split(",", 1)[1]
            try:
                img = base64.b64decode(raw)
            except Exception:
                return self._json({"ok": False, "err": "jpeg non valido"}, 400)
            if len(img) > 15 * 1024 * 1024:
                return self._json({"ok": False, "err": "immagine troppo grande"}, 400)
            fname = "shot-%s.jpg" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            with open(os.path.join(SNAP_DIR, fname), "wb") as f:
                f.write(img)
            store["shots"].insert(0, fname)
            store["shots"] = store["shots"][:100]
            print(f"[iPhoneCam] Foto salvata: {fname}")
            return self._json({"ok": True, "file": fname})
        if u.path == "/api/clear":
            store.update(
                {"offer": None, "answer": None, "ice_phone": [],
                 "ice_pc": [], "control": [], "events": []}
            )
            return self._json({"ok": True})
        return self.send_error(404)

    def do_DELETE(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/shots/"):
            name = os.path.basename(u.path[len("/api/shots/"):])
            p = os.path.join(SNAP_DIR, name)
            if os.path.basename(p) != name or name not in store["shots"]:
                return self.send_error(404)
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            store["shots"] = [s for s in store["shots"] if s != name]
            return self._json({"ok": True})
        return self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def pause_exit(msg="Premi INVIO per chiudere..."):
    # Per l'EXE: la finestra non deve sparire prima che l'utente legga l'errore.
    try:
        input(f"\n[iPhoneCam] {msg}")
    except (EOFError, KeyboardInterrupt):
        pass


def try_listen(port):
    srv = socketserver.ThreadingTCPServer(("0.0.0.0", port), H, bind_and_activate=False)
    srv.allow_reuse_address = True
    srv.daemon_threads = True
    srv.server_bind()
    srv.server_activate()
    return srv


def main():
    global PORT
    ap = argparse.ArgumentParser(description="iPhoneCam per Windows — usa l'iPhone come webcam")
    ap.add_argument("--port", type=int, default=4443)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    PORT = args.port

    # Ricostruisce la galleria da file esistenti
    for f in sorted(os.listdir(SNAP_DIR), reverse=True):
        if f.lower().endswith((".jpg", ".jpeg")):
            store["shots"].append(f)

    ips = lan_ips()
    cert, key = ensure_cert(ips)

    httpd = None
    for p in range(args.port, args.port + 10):
        try:
            httpd = try_listen(p)
            break
        except OSError:
            if p == args.port:
                print(f"[iPhoneCam] Porta {p} occupata: un'altra istanza e' gia' in esecuzione?")
                print("[iPhoneCam] Provo le porte successive...")
    if httpd is None:
        print(f"[iPhoneCam] ERRORE: porte {args.port}-{args.port + 9} tutte occupate.")
        print("[iPhoneCam] Chiudi l'altra finestra iPhoneCam (Task Manager -> iPhoneCam.exe) e riprova.")
        return pause_exit()
    PORT = httpd.server_address[1]
    if PORT != args.port:
        print(f"[iPhoneCam] Uso la porta {PORT}. Inquadra il nuovo QR!")
    with httpd:
        if cert:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            proto = "https"
        else:
            proto = "http (SOLO debug: getUserMedia su iPhone NON funzionera')"
        print(f"[iPhoneCam v{__version__}] Dashboard: {proto}://127.0.0.1:{PORT}/")
        print(f"[iPhoneCam v{__version__}] iPhone (stesso Wi-Fi o USB): https://{ips[0]}:{PORT}/phone?t={TOKEN}")
        print("[iPhoneCam] Sull'iPhone accetta l'avviso certificato UNA volta, poi Consenti fotocamera.")
        if not args.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(f"{proto}://127.0.0.1:{PORT}/")).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[iPhoneCam] Arresto.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[iPhoneCam] Arresto.")
    except Exception as e:  # mai traceback nudo nella finestra dell'utente
        print(f"\n[iPhoneCam] ERRORE: {e}")
        print("[iPhoneCam] Se il problema persiste, apri un issue su GitHub con questo messaggio.")
        pause_exit() 
