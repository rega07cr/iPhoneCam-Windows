"""iPhoneCam per Windows v2 — server HTTPS locale + signaling WebRTC + controlli remoti.

Zero app su iOS: l'iPhone apre Safari via QR e invia il video con WebRTC.
Il PC riceve nel browser (dashboard) e puo' registrare, scattare foto,
cambiare camera/torcia/qualita' da remoto via coda comandi.
"""
import argparse
import base64
import datetime
import hashlib
import http.server
import json
import os
import secrets
import socket
import socketserver
import ssl
import struct
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse, parse_qs

__version__ = "2.3.0"

PORT_GUI = 8080  # dashboard HTTP solo-locale per la finestra integrata

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
LOG_FILE = os.path.join(DATA_DIR, "iPhoneCam.log")


def logmsg(msg):
    # Log su file (l'EXE --windowed non ha console) + stdout quando c'e'.
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except OSError:
        pass

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
# Webcam virtuale (Discord / Zoom / Teams vedono l'iPhone come vera webcam).
# Il video arriva dal browser via WebSocket (JPEG) e viene inoltrato al driver
# con pyvirtualcam: backend 'unitycapture' (driver integrato in driver/) oppure
# 'obs' (se OBS e' installato). Nessun cloud, tutto in locale.
# ----------------------------------------------------------------------------
vcam_lock = threading.Lock()
vcam = {
    "active": False, "backend": None, "device": None,
    "width": 1280, "height": 720, "fps": 30,
    "cam": None, "frames": 0, "fps_actual": 0,
    "_n": 0, "_t": 0.0, "err": None, "session": 0,
    "detected": None, "detected_ts": 0.0,
}


def vcam_detect(force=False):
    if not force and vcam["detected"] and (time.time() - vcam["detected_ts"]) < 120:
        return vcam["detected"]
    res = {"ok": False, "backends": {}, "err": None}
    try:
        import pyvirtualcam
    except ImportError:
        res["err"] = "pyvirtualcam non installato (py -m pip install -r requirements.txt)"
        vcam["detected"] = res
        vcam["detected_ts"] = time.time()
        return res
    for be in ("unitycapture", "obs"):
        try:
            # Sonda con gli stessi parametri d'uso: alcuni backend rifiutano i formati bassi.
            with pyvirtualcam.Camera(width=1280, height=720, fps=30, backend=be) as c:
                res["backends"][be] = c.device
        except Exception:
            pass
    res["ok"] = bool(res["backends"])
    if not res["ok"]:
        res["err"] = ("Nessun driver trovato: esegui Registra-Webcam-Virtuale.bat "
                       "come amministratore oppure installa OBS Studio.")
    vcam["detected"] = res
    vcam["detected_ts"] = time.time()
    return res


def vcam_start(w=1280, h=720, fps=30, prefer=None):
    with vcam_lock:
        vcam_stop_locked()
        try:
            import pyvirtualcam
        except ImportError as e:
            vcam["err"] = "pyvirtualcam non installato: %s" % e
            return {"ok": False, "err": vcam["err"]}
        order = [prefer] if prefer in ("unitycapture", "obs") else ["unitycapture", "obs"]
        last = None
        for be in order:
            try:
                cam = pyvirtualcam.Camera(width=w, height=h, fps=fps, backend=be)
                vcam.update(cam=cam, backend=cam.backend, device=cam.device,
                            width=w, height=h, fps=fps, active=True,
                            frames=0, err=None, _n=0, _t=time.time())
                vcam["session"] += 1
                print(f"[iPhoneCam] Webcam virtuale attiva: {cam.device} ({cam.backend})")
                return {"ok": True, "device": cam.device, "backend": cam.backend}
            except Exception as e:
                last = e
        vcam["err"] = "Avvio fallito: %s" % (str(last)[:200] if last else "?")
        return {"ok": False, "err": vcam["err"]}


def vcam_send_jpeg(data: bytes):
    with vcam_lock:
        cam = vcam["cam"]
        if cam is None or not vcam["active"]:
            return False
        try:
            import io
            import numpy as np
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if img.size != (vcam["width"], vcam["height"]):
                img = img.resize((vcam["width"], vcam["height"]))
            cam.send(np.asarray(img))
            vcam["frames"] += 1
            vcam["_n"] += 1
            now = time.time()
            if now - vcam["_t"] >= 1.0:
                vcam["fps_actual"] = round(vcam["_n"] / max(now - vcam["_t"], 1e-3), 1)
                vcam["_n"] = 0
                vcam["_t"] = now
            return True
        except Exception as e:
            vcam["err"] = str(e)[:200]
            return False


def vcam_stop_locked():
    cam = vcam.pop("cam", None)
    if cam is not None:
        try:
            cam.close()
        except Exception:
            pass
    vcam["cam"] = None
    if vcam["active"]:
        vcam["active"] = False
        vcam["session"] += 1
        print("[iPhoneCam] Webcam virtuale fermata.")


def vcam_stop():
    with vcam_lock:
        vcam_stop_locked()
    return {"ok": True}


def vcam_status():
    det = vcam_detect()
    return {
        "active": vcam["active"], "backend": vcam["backend"],
        "device": vcam["device"], "width": vcam["width"],
        "height": vcam["height"], "fps_target": vcam["fps"],
        "fps_actual": vcam["fps_actual"], "frames_sent": vcam["frames"],
        "err": vcam["err"], "detected": det,
    }


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_recvall(conn, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = conn.recv(n - len(buf))
        except socket.timeout:
            return None  # solo attesa: il chiamante ricontrolla lo stato
        if not chunk:
            raise ConnectionError("chiuso")
        buf += chunk
    return buf


def _ws_send(conn, opcode, data=b""):
    h = bytes([0x80 | opcode])
    ln = len(data)
    if ln < 126:
        h += bytes([ln])
    elif ln < 65536:
        h += bytes([126]) + struct.pack(">H", ln)
    else:
        h += bytes([127]) + struct.pack(">Q", ln)
    conn.sendall(h + data)


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

    def _ws_frames(self):
        # Endpoint WebSocket minimale (solo server): la dashboard invia frame
        # JPEG binari che vengono inoltrati alla webcam virtuale.
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            return self.send_error(400)
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        conn = self.connection
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
        conn.settimeout(0.5)
        my_session = vcam["session"]
        waited = 0.0
        try:
            while True:
                if not vcam["active"] or vcam["session"] != my_session:
                    if waited > 8.0:
                        break
                    waited += 0.5
                    try:
                        _ws_recvall(conn, 0)
                    except ConnectionError:
                        break
                    continue
                try:
                    hdr = _ws_recvall(conn, 2)
                except ConnectionError:
                    break
                if hdr is None:
                    continue
                b1, b2 = hdr[0], hdr[1]
                op, ln = b1 & 0x0F, b2 & 0x7F
                if ln == 126:
                    ext = _ws_recvall(conn, 2)
                    if ext is None:
                        continue
                    ln = struct.unpack(">H", ext)[0]
                elif ln == 127:
                    ext = _ws_recvall(conn, 8)
                    if ext is None:
                        continue
                    ln = struct.unpack(">Q", ext)[0]
                masked = (b2 >> 7) & 1
                mask = _ws_recvall(conn, 4) if masked else None
                if mask is None and masked:
                    continue
                try:
                    payload = _ws_recvall(conn, ln) if ln else b""
                except ConnectionError:
                    break
                if payload is None:
                    continue
                if masked:
                    payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
                if op == 0x8:
                    break
                elif op == 0x9:
                    try:
                        _ws_send(conn, 0xA, payload)
                    except (ConnectionError, OSError):
                        break
                elif op == 0x2 and payload:
                    vcam_send_jpeg(payload)
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        self.close_connection = True

    # -- GET ------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/ws/frames" and self.headers.get("Upgrade", "").lower() == "websocket":
            return self._ws_frames()
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
                    "guiPort": PORT_GUI,
                    "token": TOKEN,
                    "phoneUrl": f"https://{ips[0]}:{PORT}/phone?t={TOKEN}",
                    "pcOnline": (time.time() - store["pc_seen"]) < 12,
                    "usb": any(i.startswith("172.20.10.") for i in ips),
                    "usbIp": next((i for i in ips if i.startswith("172.20.10.")), None),
                    "transport": "usb" if any(i.startswith("172.20.10.") for i in ips) else "lan",
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
            total = 0
            for f in store["shots"]:
                try:
                    total += os.path.getsize(os.path.join(SNAP_DIR, f))
                except OSError:
                    pass
            return self._json({"shots": store["shots"], "bytes": total,
                               "folder": os.path.abspath(SNAP_DIR)})
        if u.path == "/api/logs":
            try:
                with open(LOG_FILE, encoding="utf-8") as f:
                    lines = f.read().splitlines()[-80:]
            except OSError:
                lines = []
            return self._json({"lines": lines})
        if u.path == "/api/vcam":
            return self._json(vcam_status())
        if u.path == "/api/qr":
            return self._qr_png()
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

    def _qr_png(self):
        # QR generato dal server: nessun CDN, funziona anche offline.
        try:
            import qrcode
            from io import BytesIO
        except ImportError:
            return self._json({"ok": False,
                               "err": "manca 'qrcode': py -m pip install -r requirements.txt"}, 500)
        ips = lan_ips()
        url = f"https://{ips[0]}:{PORT}/phone?t={TOKEN}"
        buf = BytesIO()
        qrcode.make(url, box_size=8, border=2).save(buf, format="PNG")
        b = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    # -- POST -----------------------------------------------------------
    def do_POST(self):
        global TOKEN
        u = urlparse(self.path)
        data = self._body()

        if u.path == "/api/ping":
            store["pc_seen"] = time.time()
            return self._json({"ok": True, "n_control": len(store["control"])})
        if u.path == "/api/vcam":
            act = data.get("action")
            if act == "start":
                w = int(data.get("w", 1280) or 1280)
                h = int(data.get("h", 720) or 720)
                fps = float(data.get("fps", 30) or 30)
                r = vcam_start(w, h, min(max(fps, 5), 30), data.get("backend"))
                r.update(vcam_status())
                return self._json(r)
            if act == "stop":
                vcam_stop()
                return self._json(vcam_status())
            return self._json({"ok": False, "err": "action: start|stop"}, 400)
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
            logmsg("Offerta ricevuta da iPhone")
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
            logmsg(f"Foto salvata: {fname}")
            return self._json({"ok": True, "file": fname})
        if u.path == "/api/clear":
            store.update(
                {"offer": None, "answer": None, "ice_phone": [],
                 "ice_pc": [], "control": [], "events": []}
            )
            return self._json({"ok": True})
        if u.path == "/api/open-folder":
            try:
                os.startfile(os.path.abspath(SNAP_DIR))  # noqa: Windows
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "err": str(e)[:200]}, 500)
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
    # EXE --windowed: niente console -> popup Windows; .bat: attesa INVIO.
    try:
        input(f"\n[iPhoneCam] {msg}")
        return
    except (EOFError, KeyboardInterrupt, OSError):
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "iPhoneCam", 0x40)
    except Exception:
        pass


def try_listen(host, port):
    srv = socketserver.ThreadingTCPServer((host, port), H, bind_and_activate=False)
    srv.allow_reuse_address = True
    srv.daemon_threads = True
    srv.server_bind()
    srv.server_activate()
    return srv


def open_gui(url):
    # Finestra integrata (Edge WebView2 su Windows): tutto resta nell'app.
    try:
        import webview
    except ImportError:
        print("[iPhoneCam] 'pywebview' mancante: uso il browser. (py -m pip install -r requirements.txt)")
        return False
    try:
        webview.create_window("iPhoneCam — iPhone come webcam", url, width=1180, height=860)
        webview.start()
        return True
    except Exception as e:
        print(f"[iPhoneCam] Finestra integrata non avviata ({e}): uso il browser.")
        return False


def main():
    global PORT, PORT_GUI
    ap = argparse.ArgumentParser(description="iPhoneCam per Windows — usa l'iPhone come webcam")
    ap.add_argument("--port", type=int, default=4443)
    ap.add_argument("--no-browser", action="store_true", help="solo server, nessuna finestra")
    ap.add_argument("--browser", action="store_true", help="forza browser esterno invece della finestra integrata")
    args = ap.parse_args()

    # Ricostruisce la galleria da file esistenti
    for f in sorted(os.listdir(SNAP_DIR), reverse=True):
        if f.lower().endswith((".jpg", ".jpeg")):
            store["shots"].append(f)

    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
            os.remove(LOG_FILE)
    except OSError:
        pass
    logmsg(f"iPhoneCam v{__version__} avviato.")
    ips = lan_ips()
    cert, key = ensure_cert(ips)

    # 1) HTTPS su tutta la rete: serve all'iPhone (getUserMedia richiede TLS).
    httpsd = None
    for p in range(args.port, args.port + 10):
        try:
            httpsd = try_listen("0.0.0.0", p)
            break
        except OSError:
            if p == args.port:
                logmsg(f"Porta {p} occupata: un'altra istanza e' gia' in esecuzione?")
                logmsg("Provo le porte successive...")
    if httpsd is None:
        msg = (f"Porte {args.port}-{args.port + 9} tutte occupate. Chiudi l'altra finestra "
               "iPhoneCam (Task Manager -> iPhoneCam.exe) e riprova.")
        logmsg("ERRORE: " + msg)
        return pause_exit(msg)
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True)
    else:
        logmsg("ATTENZIONE: HTTPS disabilitato, su iPhone la fotocamera NON funzionera'.")
    PORT = httpsd.server_address[1]
    if PORT != args.port:
        logmsg(f"Uso la porta {PORT}. Inquadra il nuovo QR!")

    # 2) HTTP solo su localhost: dashboard senza avvisi di certificato.
    httpds = None
    for p in range(8080, 8090):
        try:
            httpds = try_listen("127.0.0.1", p)
            break
        except OSError:
            continue
    if httpds is None:
        msg = "Nessuna porta locale libera (8080-8089)."
        logmsg("ERRORE: " + msg)
        return pause_exit(msg)
    PORT_GUI = httpds.server_address[1]

    threading.Thread(target=httpsd.serve_forever, daemon=True).start()
    threading.Thread(target=httpds.serve_forever, daemon=True).start()

    gui_url = f"http://127.0.0.1:{PORT_GUI}/"
    logmsg(f"App: {gui_url}")
    logmsg(f"iPhone (stesso Wi-Fi o USB): https://{ips[0]}:{PORT}/phone?t={TOKEN}")
    logmsg("Sull'iPhone accetta l'avviso certificato UNA volta, poi Consenti fotocamera.")

    if args.no_browser:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            logmsg("Arresto.")
        return
    if not args.browser and open_gui(gui_url):
        return  # finestra chiusa -> i thread daemon terminano col processo
    import time as _t
    _t.sleep(1.0)
    webbrowser.open(gui_url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        logmsg("Arresto.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logmsg("Arresto.")
    except Exception as e:  # mai traceback nudo nella finestra dell'utente
        msg = (f"ERRORE: {e}\nSe il problema persiste, apri un issue su GitHub "
               "con questo messaggio.\nDettagli in iPhoneCam.log")
        logmsg(msg)
        pause_exit(msg)
