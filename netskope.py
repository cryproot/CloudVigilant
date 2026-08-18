"""
Netskope Security Audit - Single Script
Python stdlib only | No cmd/powershell | Calls System32 binaries directly
Usage: python netskope_audit.py
"""

import os, sys, socket, ssl, struct, json, subprocess, datetime
import urllib.request, http.client

S32 = r"C:\Windows\System32"
RESULTS = {}

def run(binary, args=None, timeout=15):
    try:
        r = subprocess.run(
            [os.path.join(S32, binary)] + (args or []),
            capture_output=True, text=True, timeout=timeout,
            creationflags=0x08000000
        )
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"[!] {e}"

def hdr(t):  print(f"\n{'='*60}\n  {t}\n{'='*60}")
def ok(m):   print(f"  [+] {m}")
def nfo(m):  print(f"  [*] {m}")
def wrn(m):  print(f"  [!] {m}")
def bad(m):  print(f"  [-] {m}")

def store(cat, key, val):
    RESULTS.setdefault(cat, {})[key] = val

# ── DNS query builder ─────────────────────────────────────────
def dns_query(domain, qtype=1):
    hdr = b'\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
    qname = b"".join(bytes([len(l)]) + l.encode() for l in domain.split(".")) + b'\x00'
    return hdr + qname + struct.pack("!H", qtype) + b'\x00\x01'

# ══════════════════════════════════════════════════════════════
#  RECON
# ══════════════════════════════════════════════════════════════

def recon_proxy():
    hdr("RECON: Proxy Configuration")

    # Env vars
    found = {}
    for v in ["HTTP_PROXY","HTTPS_PROXY","NO_PROXY","http_proxy","https_proxy","no_proxy"]:
        val = os.environ.get(v)
        if val:
            ok(f"{v} = {val}"); found[v] = val
        else:
            nfo(f"{v} = (not set)")
    store("proxy", "env", found)

    # Registry
    try:
        import winreg
        for hive, hname in [(winreg.HKEY_CURRENT_USER,"HKCU"),(winreg.HKEY_LOCAL_MACHINE,"HKLM")]:
            try:
                k = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
                for name in ["ProxyEnable","ProxyServer","ProxyOverride","AutoConfigURL"]:
                    try:
                        val, _ = winreg.QueryValueEx(k, name)
                        ok(f"{hname}\\{name} = {val}")
                        store("proxy", f"{hname}_{name}", val)
                    except FileNotFoundError: pass
                winreg.CloseKey(k)
            except Exception as e:
                wrn(f"{hname}: {e}")
    except ImportError:
        wrn("winreg not available (not Windows)")

    # WinHTTP
    out = run("netsh.exe", ["winhttp","show","proxy"])
    nfo(f"WinHTTP: {out[:200]}")
    store("proxy", "winhttp", out)

    # PAC file
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            pac, _ = winreg.QueryValueEx(k, "AutoConfigURL")
            ok(f"PAC URL: {pac}")
            try:
                resp = urllib.request.urlopen(pac, timeout=10)
                content = resp.read().decode("utf-8", errors="replace")
                ok(f"PAC downloaded ({len(content)} bytes)")
                for line in content.split("\n"):
                    if any(x in line for x in ["DIRECT","dnsDomainIs","shExpMatch","isInNet"]):
                        nfo(f"  {line.strip()[:120]}")
                store("proxy", "pac", content)
            except Exception as e:
                wrn(f"PAC download failed: {e}")
        except FileNotFoundError: pass
        winreg.CloseKey(k)
    except: pass


def recon_netskope():
    hdr("RECON: Netskope Detection")

    # Processes
    out = run("tasklist.exe", ["/FO","CSV","/V"])
    procs = [l for l in out.split("\n") if "netskope" in l.lower() or "stagent" in l.lower() or "nsclient" in l.lower()]
    for p in procs: ok(f"Process: {p.strip()}")
    if not procs: nfo("No Netskope processes found")
    store("netskope", "processes", procs)

    # Install paths + config files
    configs = []
    for path in [r"C:\Program Files\Netskope", r"C:\Program Files (x86)\Netskope",
                 r"C:\ProgramData\netskope", os.path.expandvars(r"%LOCALAPPDATA%\Netskope")]:
        if os.path.exists(path):
            ok(f"Found: {path}")
            try:
                for root, _, files in os.walk(path):
                    for f in files:
                        fp = os.path.join(root, f)
                        ext = f.rsplit(".",1)[-1].lower() if "." in f else ""
                        if ext in ("json","cfg","conf","ini","xml","yaml","yml","txt","log"):
                            nfo(f"  {fp} ({os.path.getsize(fp)}b)")
                            configs.append(fp)
            except PermissionError:
                wrn(f"  Permission denied: {path}")

    # Read interesting configs (steering, bypass rules)
    bypass_domains = []
    for fp in configs[:15]:
        try:
            with open(fp, "r", errors="replace") as f:
                content = f.read(50000)
            for kw in ["bypass","exception","exclude","whitelist","steering","direct"]:
                if kw in content.lower():
                    ok(f"Interesting: {fp}")
                    for i, line in enumerate(content.split("\n")):
                        if kw in line.lower():
                            nfo(f"  L{i+1}: {line.strip()[:120]}")
                            bypass_domains.append(line.strip())
                    break
        except: pass
    store("netskope", "bypass_rules", bypass_domains)

    # Registry
    try:
        import winreg
        for hive, path in [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Netskope"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\stAgentSvc")]:
            try:
                k = winreg.OpenKey(hive, path)
                ok(f"Registry: HKLM\\{path}")
                i = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(k, i)
                        nfo(f"  {name} = {val}")
                        i += 1
                    except OSError: break
                winreg.CloseKey(k)
            except: pass
    except: pass


def recon_network():
    hdr("RECON: Network & Ports")

    # Netskope local proxy ports
    ns_ports = []
    for port in [7400, 7401, 7402, 7443, 8843, 9443]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                ok(f"Netskope proxy candidate: 127.0.0.1:{port}")
                ns_ports.append(port)
            s.close()
        except: pass
    store("network", "ns_local_ports", ns_ports)

    # Interfaces (look for Netskope virtual adapter)
    out = run("ipconfig.exe", ["/all"])
    for line in out.split("\n"):
        if "netskope" in line.lower() or "nstunnel" in line.lower():
            ok(f"Netskope adapter: {line.strip()}")
        elif "dns" in line.lower() and ":" in line:
            nfo(f"  {line.strip()}")
    store("network", "ipconfig", out)


def recon_dns():
    hdr("RECON: DNS Resolution")
    domains = ["www.google.com","api.telegram.org","pastebin.com","raw.githubusercontent.com",
               "ngrok.io","discord.com","mega.nz","transfer.sh"]
    results = {}
    for d in domains:
        try:
            ips = list(set(a[4][0] for a in socket.getaddrinfo(d, 443, socket.AF_INET)))
            nfo(f"{d} -> {', '.join(ips)}")
            results[d] = ips
        except socket.gaierror as e:
            wrn(f"{d} -> BLOCKED ({e})")
            results[d] = None
    store("dns", "resolution", results)


def recon_tls():
    hdr("RECON: TLS Interception")
    results = {}
    for host in ["www.google.com","api.github.com","www.cloudflare.com","1.1.1.1"]:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, 443), timeout=10) as s:
                with ctx.wrap_socket(s, server_hostname=host) as ss:
                    cert = ss.getpeercert()
                    issuer = dict(x[0] for x in cert.get("issuer", []))
                    org = issuer.get("organizationName", "?")
                    cn = issuer.get("commonName", "?")
                    intercepted = any(k in (org+cn).lower() for k in
                                      ["netskope","zscaler","palo alto","forcepoint","bluecoat","mcafee"])
                    if intercepted:
                        wrn(f"{host}: INTERCEPTED by {org}")
                    else:
                        ok(f"{host}: clean (issuer: {org})")
                    results[host] = {"issuer": org, "cn": cn, "intercepted": intercepted}
        except Exception as e:
            bad(f"{host}: {e}")
            results[host] = {"error": str(e)}
    store("tls", "interception", results)


# ══════════════════════════════════════════════════════════════
#  BYPASS TESTS
# ══════════════════════════════════════════════════════════════

def test_direct_ip():
    hdr("BYPASS 1: Direct IP Connection (skip proxy/DNS)")
    test_host = "httpbin.org"
    try:
        ip = socket.gethostbyname(test_host)
    except:
        ip = "54.208.105.16"

    # HTTP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((ip, 80))
        s.sendall(f"GET /ip HTTP/1.1\r\nHost: {test_host}\r\nConnection: close\r\n\r\n".encode())
        resp = b""
        while True:
            c = s.recv(4096)
            if not c: break
            resp += c
        s.close()
        d = resp.decode("utf-8", errors="replace")
        if "200" in d:
            ok(f"Direct HTTP via IP WORKS ({len(d)}b)")
            store("bypass", "direct_http", "success")
        else:
            wrn(f"Unexpected response: {d[:150]}")
    except Exception as e:
        bad(f"Direct HTTP failed: {e}")
        store("bypass", "direct_http", str(e))

    # HTTPS
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        s = socket.create_connection((ip, 443), timeout=10)
        ss = ctx.wrap_socket(s, server_hostname=test_host)
        ss.sendall(f"GET /ip HTTP/1.1\r\nHost: {test_host}\r\nConnection: close\r\n\r\n".encode())
        resp = b""
        while True:
            c = ss.recv(4096)
            if not c: break
            resp += c
        ss.close()
        if b"200" in resp:
            ok("Direct HTTPS via IP WORKS")
            store("bypass", "direct_https", "success")
    except Exception as e:
        bad(f"Direct HTTPS failed: {e}")
        store("bypass", "direct_https", str(e))


def test_no_proxy():
    hdr("BYPASS 2: urllib Without Proxy")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        resp = opener.open("https://httpbin.org/ip", timeout=10)
        body = resp.read().decode()
        ok(f"No-proxy urllib WORKS: {body[:100]}")
        store("bypass", "no_proxy", "success")
    except Exception as e:
        bad(f"Failed: {e}")
        store("bypass", "no_proxy", str(e))


def test_alt_ports():
    hdr("BYPASS 3: Non-Standard Ports")
    targets = [("1.1.1.1",53),("1.1.1.1",853),("1.1.1.1",443),("8.8.8.8",53),("8.8.8.8",443)]
    for host, port in targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            if s.connect_ex((host, port)) == 0:
                ok(f"{host}:{port} REACHABLE")
            else:
                bad(f"{host}:{port} blocked")
            s.close()
        except Exception as e:
            bad(f"{host}:{port} error: {e}")


def test_ipv6():
    hdr("BYPASS 4: IPv6 (often not intercepted)")
    if not socket.has_ipv6:
        bad("No IPv6 support"); return
    for host in ["www.google.com", "ipv6.google.com"]:
        try:
            addrs = socket.getaddrinfo(host, 443, socket.AF_INET6)
            if not addrs: bad(f"{host}: no AAAA record"); continue
            addr = addrs[0][4]
            nfo(f"{host} -> {addr[0]}")
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(addr)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ss = ctx.wrap_socket(s, server_hostname=host)
            cert = ss.getpeercert()
            issuer = dict(x[0] for x in cert.get("issuer", []))
            org = issuer.get("organizationName", "?")
            if "netskope" not in org.lower():
                ok(f"{host} IPv6 NOT intercepted (issuer: {org})")
                store("bypass", "ipv6", "not_intercepted")
            else:
                wrn(f"{host} IPv6 IS intercepted")
                store("bypass", "ipv6", "intercepted")
            ss.close()
        except Exception as e:
            bad(f"{host} IPv6: {e}")


def test_sys32_downloads():
    hdr("BYPASS 5: System32 Download Tools")
    tmp = os.environ.get("TEMP", ".")

    # certutil
    f1 = os.path.join(tmp, "ns_test_cu.txt")
    out = run("certutil.exe", ["-urlcache","-split","-f","http://httpbin.org/ip", f1])
    if os.path.exists(f1):
        ok(f"certutil download WORKS ({os.path.getsize(f1)}b)")
        os.remove(f1)
        store("bypass", "certutil", "success")
    else:
        bad(f"certutil failed: {out[:150]}")
        store("bypass", "certutil", "failed")

    # bitsadmin
    f2 = os.path.join(tmp, "ns_test_ba.txt")
    out = run("bitsadmin.exe", ["/transfer","nsaudit","/download","/priority","foreground",
              "http://httpbin.org/ip", f2], timeout=25)
    if os.path.exists(f2):
        ok(f"bitsadmin download WORKS ({os.path.getsize(f2)}b)")
        os.remove(f2)
        store("bypass", "bitsadmin", "success")
    else:
        bad(f"bitsadmin failed: {out[:150]}")
        store("bypass", "bitsadmin", "failed")


def test_dns_tunnel():
    hdr("BYPASS 6: DNS Tunneling Feasibility")
    for ip, name in [("8.8.8.8","Google"),("1.1.1.1","Cloudflare")]:
        # UDP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(5)
            s.sendto(dns_query("example.com"), (ip, 53))
            resp, _ = s.recvfrom(1024); s.close()
            if len(resp) > 12:
                ok(f"{name} DNS ({ip}) responds ({len(resp)}b)")
                store("bypass", f"dns_udp_{ip}", "reachable")
        except Exception as e:
            bad(f"{name} DNS ({ip}): {e}")

    # TXT records
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(5)
        s.sendto(dns_query("google.com", qtype=16), ("8.8.8.8", 53))
        resp, _ = s.recvfrom(4096); s.close()
        if len(resp) > 12:
            ok(f"TXT queries work ({len(resp)}b) - DNS tunneling FEASIBLE")
            store("bypass", "dns_txt", "feasible")
    except Exception as e:
        bad(f"TXT queries: {e}")

    # DNS over TCP
    try:
        q = dns_query("example.com")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(("8.8.8.8", 53))
        s.sendall(struct.pack("!H", len(q)) + q)
        rlen = struct.unpack("!H", s.recv(2))[0]
        s.recv(rlen); s.close()
        ok("DNS over TCP works")
        store("bypass", "dns_tcp", "works")
    except Exception as e:
        bad(f"DNS/TCP: {e}")


def test_whitelisted():
    hdr("BYPASS 7: Whitelisted Domains (not TLS-inspected)")
    domains = ["login.microsoftonline.com","graph.microsoft.com","outlook.office365.com",
               "teams.microsoft.com","management.azure.com","s3.amazonaws.com",
               "accounts.google.com","github.com","api.github.com","cdn.jsdelivr.net"]
    clean = []
    for d in domains:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((d, 443), timeout=5) as s:
                with ctx.wrap_socket(s, server_hostname=d) as ss:
                    cert = ss.getpeercert()
                    org = dict(x[0] for x in cert.get("issuer",[])).get("organizationName","?")
                    if "netskope" not in org.lower():
                        ok(f"{d} - NOT intercepted ({org})")
                        clean.append(d)
                    else:
                        nfo(f"{d} - intercepted by Netskope")
        except Exception as e:
            bad(f"{d}: {e}")

    if clean:
        ok(f"\n  {len(clean)} domains bypass TLS inspection - usable for tunneling/fronting")
    store("bypass", "whitelisted_clean", clean)


def test_connect_tunnel():
    hdr("BYPASS 8: HTTP CONNECT via Local Proxy")
    for port in [7400, 7401, 8080, 3128]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                s.close(); continue
            s.sendall(f"CONNECT httpbin.org:443 HTTP/1.1\r\nHost: httpbin.org:443\r\n\r\n".encode())
            resp = s.recv(4096).decode("utf-8", errors="replace")
            s.close()
            if "200" in resp:
                ok(f"CONNECT tunnel via :{port} WORKS!")
                store("bypass", f"connect_{port}", "success")
            elif "403" in resp or "407" in resp:
                wrn(f"Proxy :{port} denied CONNECT")
        except: pass


# ══════════════════════════════════════════════════════════════
#  SUMMARY
# ══════════════════════════════════════════════════════════════

def summary():
    hdr("AUDIT SUMMARY - BYPASS VECTORS")
    bypass = RESULTS.get("bypass", {})
    vectors = []
    checks = [
        ("direct_http",   "Direct HTTP via IP (skip proxy/DNS)"),
        ("direct_https",  "Direct HTTPS via IP"),
        ("no_proxy",      "urllib with empty ProxyHandler"),
        ("ipv6",          "IPv6 not intercepted by Netskope"),
        ("certutil",      "certutil.exe file download"),
        ("bitsadmin",     "bitsadmin.exe file download"),
        ("dns_txt",       "DNS tunneling (TXT records)"),
        ("dns_tcp",       "DNS over TCP tunneling"),
    ]
    for key, desc in checks:
        val = bypass.get(key, "")
        if val in ("success","feasible","works","not_intercepted","reachable"):
            ok(f"[VECTOR] {desc}")
            vectors.append(desc)

    clean = bypass.get("whitelisted_clean", [])
    if clean:
        ok(f"[VECTOR] {len(clean)} domains not TLS-inspected: {', '.join(clean[:5])}")
        vectors.append(f"{len(clean)} whitelisted domains")

    for key, val in bypass.items():
        if key.startswith("connect_") and val == "success":
            ok(f"[VECTOR] HTTP CONNECT tunnel on port {key.split('_')[1]}")
            vectors.append("HTTP CONNECT tunnel")

    if not vectors:
        nfo("No bypass vectors found - Netskope appears well configured")
    else:
        print(f"\n  Total vectors found: {len(vectors)}")

    # Save report
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    rpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"report_{ts}.json")
    try:
        with open(rpath, "w") as f:
            json.dump(RESULTS, f, indent=2, default=str)
        ok(f"Report saved: {rpath}")
    except Exception as e:
        wrn(f"Cannot save report: {e}")


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(r"""
   _  __    __      __                  ___          ___ __
  / |/ /__ / /____ / /_____  ___  ___  / _ |__ _____/ (_) /_
 /    / -_) __(_-</  '_/ _ \/ _ \/ -_)/ __ / // / _  / / __/
/_/|_/\__/\__/___/_/\_\\___/ .__/\__//_/ |_\_,_/\_,_/_/\__/
                          /_/  stdlib-only | authorized use
    """)
    nfo(f"Date: {datetime.datetime.now()}")
    nfo(f"Host: {os.environ.get('COMPUTERNAME', os.environ.get('HOSTNAME','?'))}")
    nfo(f"User: {os.environ.get('USERNAME', os.environ.get('USER','?'))}")
    nfo(f"Python: {sys.version.split()[0]}")

    recon_proxy()
    recon_netskope()
    recon_network()
    recon_dns()
    recon_tls()

    test_direct_ip()
    test_no_proxy()
    test_alt_ports()
    test_ipv6()
    test_sys32_downloads()
    test_dns_tunnel()
    test_whitelisted()
    test_connect_tunnel()

    summary()
