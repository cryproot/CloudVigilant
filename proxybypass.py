"""
Local HTTPS Proxy - Netskope Bypass
Renames python.exe to a whitelisted process name so the steering
driver lets outbound connections through directly.
Double-click to run. No configuration needed.
"""

import socket
import threading
import select
import subprocess
import os
import sys
import shutil
import time

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8888
BUFFER = 65536

WHITELISTED_NAMES = ["vpnagent.exe", "nsbrowser.exe", "pdt_plame.exe"]

BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def current_process_name():
    return os.path.basename(sys.executable).lower()


def is_whitelisted():
    name = current_process_name()
    return any(name == w.lower() for w in WHITELISTED_NAMES)


def relaunch_as_whitelisted():
    """Copy python.exe as a whitelisted process name and relaunch."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.abspath(__file__)

    for wname in WHITELISTED_NAMES:
        target = os.path.join(script_dir, wname)
        if os.path.exists(target):
            print(f"  [*] {wname} ya existe, usando ese...")
        else:
            print(f"  [*] Copiando python.exe como {wname}...")
            try:
                shutil.copy2(sys.executable, target)
                print(f"  [+] Creado: {target}")
            except PermissionError:
                print(f"  [!] Sin permiso para copiar a {target}, intentando otro...")
                continue
            except Exception as e:
                print(f"  [!] Error: {e}, intentando otro...")
                continue

        print(f"  [*] Relanzando como {wname}...")
        print(f"  [*] Esto va a abrir una nueva ventana.\n")

        args = [target, script_path] + sys.argv[1:]
        subprocess.Popen(args, creationflags=0x00000010)  # CREATE_NEW_CONSOLE
        return True

    return False


def find_browser():
    for path in BROWSER_PATHS:
        if os.path.exists(path):
            return path
    return None


def launch_browser(url="https://facebook.com"):
    browser = find_browser()
    if not browser:
        print(f"  [!] No se encontro Chrome ni Edge")
        print(f'  [!] Abre manualmente:')
        print(f'  [!]   msedge.exe --proxy-server="{LISTEN_HOST}:{LISTEN_PORT}" {url}')
        return

    name = "Edge" if "edge" in browser.lower() else "Chrome"
    print(f"  [*] Abriendo {name}...")
    subprocess.Popen(
        [
            browser,
            f"--proxy-server={LISTEN_HOST}:{LISTEN_PORT}",
            "--new-window",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    print(f"  [+] {name} abierto -> {url}")


def relay(src, dst):
    try:
        while True:
            r, _, _ = select.select([src], [], [], 30)
            if not r:
                break
            data = src.recv(BUFFER)
            if not data:
                break
            dst.sendall(data)
    except:
        pass


def handle_connect(client, host, port):
    try:
        remote = socket.create_connection((host, port), timeout=10)
    except Exception as e:
        client.sendall(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{e}".encode())
        client.close()
        return

    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    t1 = threading.Thread(target=relay, args=(client, remote), daemon=True)
    t2 = threading.Thread(target=relay, args=(remote, client), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    remote.close()
    client.close()


def handle_http(client, method, url, headers_raw):
    try:
        url_body = url.split("://", 1)[1]
        host_path = url_body.split("/", 1)
        host_port = host_path[0]
        path = "/" + host_path[1] if len(host_path) > 1 else "/"

        if ":" in host_port:
            host, port = host_port.rsplit(":", 1)
            port = int(port)
        else:
            host, port = host_port, 80

        remote = socket.create_connection((host, port), timeout=10)
        rebuilt = f"{method} {path} HTTP/1.1\r\n{headers_raw}\r\n"
        remote.sendall(rebuilt.encode())

        while True:
            r, _, _ = select.select([remote], [], [], 15)
            if not r:
                break
            data = remote.recv(BUFFER)
            if not data:
                break
            client.sendall(data)
        remote.close()
    except Exception as e:
        client.sendall(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{e}".encode())
    finally:
        client.close()


def handle_client(client, addr):
    try:
        raw = client.recv(BUFFER)
        if not raw:
            client.close()
            return
        first_line, rest = raw.split(b"\r\n", 1)
        parts = first_line.decode().split(" ")
        method = parts[0]

        if method == "CONNECT":
            target = parts[1]
            if ":" in target:
                host, port = target.rsplit(":", 1)
                port = int(port)
            else:
                host, port = target, 443
            print(f"  CONNECT  {host}:{port}")
            handle_connect(client, host, port)
        else:
            url = parts[1]
            print(f"  {method:7s}  {url[:80]}")
            handle_http(client, method, url, rest.decode(errors="replace"))
    except Exception as e:
        print(f"  [!] {e}")
        try: client.close()
        except: pass


def run_proxy():
    # Parse args
    url = "https://facebook.com"
    no_browser = "--no-browser" in sys.argv
    for i, arg in enumerate(sys.argv):
        if arg == "--url" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]
            url = target if target.startswith("http") else "https://" + target

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(50)

    proc = current_process_name()
    print(f"""
  =============================================
    Bypass Proxy  |  {LISTEN_HOST}:{LISTEN_PORT}
    Proceso:  {proc}  (whitelisted: {is_whitelisted()})
  =============================================
    """)

    if not no_browser:
        def open_delayed():
            time.sleep(1)
            launch_browser(url)
        threading.Thread(target=open_delayed, daemon=True).start()

    try:
        while True:
            client, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(client, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n  Proxy detenido.")
        server.close()


def main():
    print(f"""
  =============================================
    Netskope Bypass Proxy
  =============================================
  Proceso actual: {current_process_name()}
  Whitelisted:    {is_whitelisted()}
    """)

    if is_whitelisted():
        print("  [+] Ya corremos como proceso whitelisted!")
        print("  [+] Netskope steering driver nos deja pasar.\n")
        run_proxy()
    else:
        print("  [-] python.exe NO esta whitelisted en Netskope.")
        print("  [*] Relanzo como proceso whitelisted...\n")
        if not relaunch_as_whitelisted():
            print("  [!] No se pudo relanzar con ningun nombre whitelisted.")
            print("  [!] Intentando como python.exe de todos modos...\n")
            run_proxy()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  [!] Error: {e}")
    finally:
        input("\n  Presiona Enter para cerrar...")
