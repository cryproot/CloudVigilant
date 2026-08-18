"""
Local HTTPS Proxy - Netskope Bypass
Connects directly to destination IPs, skipping the corporate proxy.
Uses only Python stdlib. No pip needed.

Usage:
  python bypass_proxy.py                  # proxy + abre Edge con facebook.com
  python bypass_proxy.py --no-browser     # solo proxy, sin abrir browser
  python bypass_proxy.py --url google.com # abre Edge con otro sitio
"""

import socket
import threading
import select
import subprocess
import os
import sys
import time

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8888
BUFFER = 65536

# Rutas comunes de browsers en Windows
BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_browser():
    for path in BROWSER_PATHS:
        if os.path.exists(path):
            return path
    return None


def launch_browser(url="https://facebook.com"):
    browser = find_browser()
    if not browser:
        print("  [!] No se encontro Chrome ni Edge")
        print(f"  [!] Abre manualmente un browser con:")
        print(f'  [!]   "ruta\\browser.exe" --proxy-server="{LISTEN_HOST}:{LISTEN_PORT}" {url}')
        return

    name = "Edge" if "edge" in browser.lower() else "Chrome"
    print(f"  [*] Abriendo {name} con proxy override...")
    print(f"  [*] Browser: {browser}")

    subprocess.Popen(
        [
            browser,
            f"--proxy-server={LISTEN_HOST}:{LISTEN_PORT}",
            "--ignore-certificate-errors",
            "--new-window",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    print(f"  [+] {name} abierto en {url}")


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
        try:
            client.close()
        except:
            pass


def main():
    # Parse args
    no_browser = "--no-browser" in sys.argv
    url = "https://facebook.com"
    for i, arg in enumerate(sys.argv):
        if arg == "--url" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]
            if not target.startswith("http"):
                target = "https://" + target
            url = target

    # Start proxy
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(50)

    print(f"""
  =============================================
    Bypass Proxy  |  {LISTEN_HOST}:{LISTEN_PORT}
  =============================================
  El browser se abre automaticamente con
  --proxy-server apuntando aqui (override GPO)

  Ctrl+C para detener
  =============================================
    """)

    # Launch browser after small delay
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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  [!] Error: {e}")
        input("\n  Presiona Enter para cerrar...")
    except KeyboardInterrupt:
        print("\n  Cerrado.")
    finally:
        input("\n  Presiona Enter para cerrar la ventana...")
