"""
tools/recon.py - Windows-native reconnaissance and intelligence gathering tools.

All tools use PowerShell/native Windows cmdlets first.
If an optional external tool (nmap, tshark) is required but not installed,
the tool returns a structured message with the recommended winget install command.
"""
import subprocess
import json
import socket
import ssl
import urllib.request
import urllib.error
from typing import Optional


def _run_ps(command: str, timeout: int = 30) -> dict:
    """Helper: run a PowerShell command and return structured output."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def dns_lookup(hostname: str, record_type: str = "A") -> dict:
    """
    Resolve DNS records for a hostname using native PowerShell Resolve-DnsName.
    
    Args:
        hostname: The target hostname or domain to resolve.
        record_type: DNS record type — A, AAAA, MX, NS, TXT, CNAME, SOA (default: A)
    
    Returns:
        Dict with records list and raw output.
    """
    ps_cmd = f"Resolve-DnsName -Name '{hostname}' -Type {record_type} | Select-Object Name, Type, IPAddress, NameHost, Strings | ConvertTo-Json -Depth 3"
    result = _run_ps(ps_cmd, timeout=15)
    if result.get("success") and result.get("stdout"):
        try:
            records = json.loads(result["stdout"])
            if isinstance(records, dict):
                records = [records]
            return {"success": True, "hostname": hostname, "type": record_type, "records": records}
        except json.JSONDecodeError:
            return {"success": True, "hostname": hostname, "type": record_type, "raw": result["stdout"]}
    return {"success": False, "hostname": hostname, "error": result.get("error") or result.get("stderr", "DNS lookup failed")}


def ping_sweep(cidr: str, timeout_ms: int = 500) -> dict:
    """
    Discover live hosts in a subnet using parallel PowerShell ping sweep.
    
    Args:
        cidr: Target network in CIDR notation (e.g., 192.168.1.0/24) OR an IP range like 192.168.1.1-50.
        timeout_ms: Ping timeout in milliseconds (default: 500).
    
    Returns:
        Dict with list of live hosts and their response times.
    """
    # Build a quick PS parallel ping sweep
    ps_cmd = f"""
$network = '{cidr}'
$timeout = {timeout_ms}

# Handle simple /24 CIDR
if ($network -match '^(\\d+\\.\\d+\\.\\d+)\\.\\d+/24$') {{
    $base = $Matches[1]
    $hosts = 1..254 | ForEach-Object {{ "$base.$_" }}
}} elseif ($network -match '^(\\d+\\.\\d+\\.\\d+\\.)([0-9]+)-([0-9]+)$') {{
    $base = $Matches[1]
    $start = [int]$Matches[2]; $end = [int]$Matches[3]
    $hosts = $start..$end | ForEach-Object {{ "$base$_" }}
}} else {{
    $hosts = @($network)
}}

$results = @()
foreach ($ip in $hosts) {{
    $ping = New-Object System.Net.NetworkInformation.Ping
    try {{
        $reply = $ping.Send($ip, {timeout_ms})
        if ($reply.Status -eq 'Success') {{
            $results += [PSCustomObject]@{{ IP = $ip; Status = 'up'; RTT = $reply.RoundtripTime }}
        }}
    }} catch {{ }}
}}

$results | ConvertTo-Json -Depth 2
"""
    result = _run_ps(ps_cmd, timeout=120)
    if result.get("success") and result.get("stdout"):
        try:
            hosts = json.loads(result["stdout"])
            if isinstance(hosts, dict):
                hosts = [hosts]
            return {"success": True, "cidr": cidr, "live_hosts": hosts, "count": len(hosts)}
        except json.JSONDecodeError:
            return {"success": True, "cidr": cidr, "raw": result["stdout"]}
    return {"success": False, "cidr": cidr, "error": result.get("error") or result.get("stderr", "Ping sweep failed")}


def port_scan(target: str, ports: str = "22,80,443,445,3389,8080,8443", timeout_ms: int = 1000) -> dict:
    """
    Scan TCP ports on a target using native PowerShell Test-NetConnection.
    For larger port ranges, checks if nmap is installed and uses it.
    
    Args:
        target: IP address or hostname to scan.
        ports: Comma-separated port numbers or range like '1-1024' (default: common ports).
        timeout_ms: Connection timeout in milliseconds (default: 1000).
    
    Returns:
        Dict with list of open/closed port results.
    """
    # Check if it's a range or list
    if "-" in ports and "," not in ports:
        # Range — try nmap first
        nmap_check = _run_ps("Get-Command nmap -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name")
        if nmap_check.get("stdout", "").strip():
            nmap_result = _run_ps(f"nmap -p {ports} --open -oX - {target}", timeout=120)
            return {
                "success": nmap_result.get("success", False),
                "target": target,
                "ports": ports,
                "tool": "nmap",
                "raw": nmap_result.get("stdout", ""),
                "error": nmap_result.get("stderr") if not nmap_result.get("success") else None
            }
        else:
            return {
                "success": False,
                "target": target,
                "ports": ports,
                "error": "Port range scan requires nmap.",
                "install_hint": "winget install Insecure.Nmap"
            }

    # List of specific ports — use Test-NetConnection
    port_list = [p.strip() for p in ports.split(",")]
    ps_cmd = f"""
$target = '{target}'
$timeout = {timeout_ms}
$ports = @({','.join(port_list)})

$results = $ports | ForEach-Object {{
    $port = $_
    try {{
        $tcp = New-Object System.Net.Sockets.TcpClient
        $conn = $tcp.BeginConnect($target, $port, $null, $null)
        $wait = $conn.AsyncWaitHandle.WaitOne($timeout, $false)
        if ($wait -and $tcp.Connected) {{
            $tcp.EndConnect($conn) | Out-Null
            [PSCustomObject]@{{ Port=$port; State='open' }}
        }} else {{
            [PSCustomObject]@{{ Port=$port; State='closed' }}
        }}
        $tcp.Close()
    }} catch {{
        [PSCustomObject]@{{ Port=$port; State='error'; Error=$_.Exception.Message }}
    }}
}}
$results | ConvertTo-Json -Depth 2
"""
    result = _run_ps(ps_cmd, timeout=60)
    if result.get("success") and result.get("stdout"):
        try:
            ports_result = json.loads(result["stdout"])
            if isinstance(ports_result, dict):
                ports_result = [ports_result]
            open_ports = [p for p in ports_result if p.get("State") == "open"]
            return {
                "success": True,
                "target": target,
                "tool": "Test-NetConnection (native)",
                "all_ports": ports_result,
                "open_ports": open_ports,
                "open_count": len(open_ports)
            }
        except json.JSONDecodeError:
            return {"success": True, "target": target, "raw": result["stdout"]}
    return {"success": False, "target": target, "error": result.get("error") or result.get("stderr", "Port scan failed")}


def http_headers_check(url: str) -> dict:
    """
    Fetch HTTP response headers and analyze security posture of a web endpoint.
    
    Args:
        url: Full URL to inspect (e.g., https://example.com).
    
    Returns:
        Dict with headers and security analysis notes.
    """
    ps_cmd = f"""
try {{
    $response = Invoke-WebRequest -Uri '{url}' -Method HEAD -UseBasicParsing -ErrorAction Stop -TimeoutSec 10
    $headers = @{{}}
    $response.Headers.GetEnumerator() | ForEach-Object {{ $headers[$_.Key] = $_.Value }}
    @{{
        StatusCode = $response.StatusCode
        Headers = $headers
    }} | ConvertTo-Json -Depth 3
}} catch {{
    @{{ Error = $_.Exception.Message }} | ConvertTo-Json
}}
"""
    result = _run_ps(ps_cmd, timeout=20)
    if result.get("success") and result.get("stdout"):
        try:
            data = json.loads(result["stdout"])
            if "Error" in data:
                return {"success": False, "url": url, "error": data["Error"]}
            
            headers = data.get("Headers", {})
            # Security analysis
            security_notes = []
            missing_headers = []
            for h in ["Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options",
                      "Content-Security-Policy", "Referrer-Policy", "Permissions-Policy"]:
                if h.lower() not in {k.lower() for k in headers}:
                    missing_headers.append(h)
            if missing_headers:
                security_notes.append(f"Missing security headers: {', '.join(missing_headers)}")
            if "Server" in headers:
                security_notes.append(f"Server fingerprint exposed: {headers['Server']}")
            
            return {
                "success": True,
                "url": url,
                "status_code": data.get("StatusCode"),
                "headers": headers,
                "security_notes": security_notes
            }
        except json.JSONDecodeError:
            return {"success": True, "url": url, "raw": result["stdout"]}
    return {"success": False, "url": url, "error": result.get("error") or result.get("stderr", "HTTP check failed")}


def http_get(url: str, max_chars: int = 20000, timeout_sec: int = 20) -> dict:
    """
    Perform a plain HTTP GET of a URL and return the response body (HTML/text).

    This is the right tool for "GET/fetch/download the HTML of <site>" or
    "retrieve and analyze the page at <url>" — NOT capture_packets/analyze_pcapng
    (packet capture is for sniffing traffic, not for fetching a web page).

    Args:
        url: Full URL to fetch (e.g. http://192.168.1.1/ or http://host/index.html).
        max_chars: Maximum number of body characters to return (default: 20000).
        timeout_sec: Request timeout in seconds (default: 20).

    Returns:
        Dict with status_code, headers, content (truncated), content_length.
    """
    ps_cmd = f"""
try {{
    $response = Invoke-WebRequest -Uri '{url}' -UseBasicParsing -ErrorAction Stop -TimeoutSec {timeout_sec}
    $headers = @{{}}
    $response.Headers.GetEnumerator() | ForEach-Object {{ $headers[$_.Key] = $_.Value }}
    @{{
        StatusCode = [int]$response.StatusCode
        Headers = $headers
        Content = $response.Content
    }} | ConvertTo-Json -Depth 4 -Compress
}} catch {{
    @{{ Error = $_.Exception.Message }} | ConvertTo-Json -Compress
}}
"""
    result = _run_ps(ps_cmd, timeout=timeout_sec + 10)
    if result.get("success") and result.get("stdout"):
        try:
            data = json.loads(result["stdout"])
        except json.JSONDecodeError:
            return {"success": True, "url": url, "raw": result["stdout"][:max_chars]}
        if "Error" in data:
            return {"success": False, "url": url, "error": data["Error"]}
        content = data.get("Content") or ""
        if not isinstance(content, str):
            content = str(content)
        truncated = len(content) > max_chars
        return {
            "success": True,
            "url": url,
            "status_code": data.get("StatusCode"),
            "headers": data.get("Headers", {}),
            "content_length": len(content),
            "truncated": truncated,
            "content": content[:max_chars],
        }
    return {"success": False, "url": url, "error": result.get("error") or result.get("stderr", "HTTP GET failed")}


def try_http_login(
    url: str,
    user: str,
    password: str,
    method: str = "auto",
    username_field: str = "username",
    password_field: str = "password",
    timeout_sec: int = 15,
) -> dict:
    """
    Attempt to authenticate to an HTTP endpoint with a username and password.

    Tries HTTP Basic auth and/or a standard form POST and returns a heuristic
    verdict on whether the credentials were accepted. This is the right tool for
    "try user X with password Y against <site>" — NOT hash_identify/crack_hash
    (a known plaintext password is not a hash to crack).

    Args:
        url: Target URL (e.g. http://192.168.1.1 or http://host/login).
        user: Username to try.
        password: Password to try.
        method: 'auto' (basic then form), 'basic', or 'form'.
        username_field: Form field name for the username (form/auto POST).
        password_field: Form field name for the password (form/auto POST).
        timeout_sec: Per-request timeout in seconds.

    Returns:
        Dict with per-attempt results (status code, likely_success, evidence)
        and an overall authenticated verdict. Sends traffic to the target host.
    """
    if not url or not str(url).strip():
        return {"success": False, "error": "url is required."}
    m = (method or "auto").lower()
    if m not in ("auto", "basic", "form"):
        return {"success": False, "error": f"Unknown method '{method}'. Use auto|basic|form."}

    # PowerShell does the request so behavior matches the rest of recon.py.
    # Heuristics: a 2xx/3xx that is NOT obviously a re-served login page, or a
    # Set-Cookie session, suggests success; common failure markers suggest failure.
    ps_cmd = f"""
$ErrorActionPreference = 'Stop'
$url = '{url}'
$user = '{user}'
$pass = '{password}'
$method = '{m}'
$uField = '{username_field}'
$pField = '{password_field}'
$timeout = {int(timeout_sec)}

$failMarkers = @('invalid','incorrect','failed','wrong password','authentication failed','login failed','denied','try again')

function Test-Login($desc, $invoke) {{
    $r = [ordered]@{{ attempt = $desc; status = $null; final_url = $null; length = 0; set_cookie = $false; likely_success = $false; note = '' }}
    try {{
        $resp = & $invoke
        $r.status = [int]$resp.StatusCode
        $r.length = ($resp.Content | Measure-Object -Character).Characters
        try {{ $r.final_url = $resp.BaseResponse.ResponseUri.AbsoluteUri }} catch {{}}
        $r.set_cookie = [bool]($resp.Headers['Set-Cookie'])
        $body = ($resp.Content | Out-String).ToLower()
        $hasFail = $false
        foreach ($mk in $failMarkers) {{ if ($body.Contains($mk)) {{ $hasFail = $true; break }} }}
        if ($r.status -ge 200 -and $r.status -lt 400 -and -not $hasFail) {{ $r.likely_success = $true }}
        if ($hasFail) {{ $r.note = 'failure marker in body' }}
    }} catch {{
        $exresp = $_.Exception.Response
        if ($exresp) {{
            try {{ $r.status = [int]$exresp.StatusCode }} catch {{}}
        }}
        if ($r.status -eq 401 -or $r.status -eq 403) {{ $r.note = 'rejected (auth failed)' }}
        else {{ $r.note = $_.Exception.Message }}
    }}
    return [PSCustomObject]$r
}}

$results = @()

if ($method -eq 'basic' -or $method -eq 'auto') {{
    $secpass = ConvertTo-SecureString $pass -AsPlainText -Force
    $cred = New-Object System.Management.Automation.PSCredential($user, $secpass)
    $results += Test-Login 'basic' {{ Invoke-WebRequest -Uri $url -Credential $cred -TimeoutSec $timeout -UseBasicParsing -MaximumRedirection 3 }}
}}

if ($method -eq 'form' -or $method -eq 'auto') {{
    $bodyHash = @{{ $uField = $user; $pField = $pass }}
    $results += Test-Login 'form' {{ Invoke-WebRequest -Uri $url -Method POST -Body $bodyHash -TimeoutSec $timeout -UseBasicParsing -MaximumRedirection 3 }}
}}

$results | ConvertTo-Json -Depth 4
"""
    result = _run_ps(ps_cmd, timeout=timeout_sec * 4 + 10)
    if result.get("success") and result.get("stdout"):
        try:
            attempts = json.loads(result["stdout"])
            if isinstance(attempts, dict):
                attempts = [attempts]
            authenticated = any(a.get("likely_success") for a in attempts)
            return {
                "success": True,
                "url": url,
                "user": user,
                "authenticated": authenticated,
                "verdict": "credentials likely ACCEPTED" if authenticated
                           else "credentials likely REJECTED",
                "attempts": attempts,
                "note": "Heuristic verdict from HTTP status + body markers; verify manually for high-stakes use.",
            }
        except json.JSONDecodeError:
            return {"success": True, "url": url, "raw": result["stdout"]}
    return {"success": False, "url": url, "error": result.get("error") or result.get("stderr", "Login attempt failed")}


def ssl_analysis(hostname: str, port: int = 443) -> dict:
    """
    Analyze the SSL/TLS certificate and configuration of a remote host using Python's ssl module.
    
    Args:
        hostname: Target hostname to connect to.
        port: TLS port (default: 443).
    
    Returns:
        Dict with certificate details, expiry, cipher suite, and security notes.
    """
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                cipher = ssock.cipher()
                version = ssock.version()
        
        # Parse cert details
        subject = dict(x[0] for x in cert.get("subject", []))
        issuer = dict(x[0] for x in cert.get("issuer", []))
        not_after = cert.get("notAfter", "")
        sans = [v for _type, v in cert.get("subjectAltName", [])]
        
        # Check for weak protocols
        security_notes = []
        if version in ("TLSv1", "TLSv1.1", "SSLv3", "SSLv2"):
            security_notes.append(f"Weak TLS version in use: {version}")
        if cipher and cipher[0] and ("RC4" in cipher[0] or "DES" in cipher[0] or "NULL" in cipher[0]):
            security_notes.append(f"Weak cipher suite: {cipher[0]}")
        
        return {
            "success": True,
            "hostname": hostname,
            "port": port,
            "tls_version": version,
            "cipher": cipher[0] if cipher else None,
            "subject": subject,
            "issuer": issuer,
            "not_after": not_after,
            "san": sans,
            "security_notes": security_notes
        }
    except ssl.SSLCertVerificationError as e:
        return {"success": False, "hostname": hostname, "error": f"SSL verification failed: {e}"}
    except Exception as e:
        return {"success": False, "hostname": hostname, "error": str(e)}


def cve_lookup(keyword: str, max_results: int = 5) -> dict:
    """
    Look up recent CVEs from the NIST NVD API by keyword or CVE ID.
    
    Args:
        keyword: Search keyword (product name, CVE ID like 'CVE-2024-1234', or technology).
        max_results: Maximum number of results to return (default: 5).
    
    Returns:
        Dict with list of CVE records including description, severity, and CVSS score.
    """
    try:
        import urllib.parse
        query = urllib.parse.quote(keyword)
        url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?keywordSearch={query}&resultsPerPage={max_results}"
        req = urllib.request.Request(url, headers={"User-Agent": "PulseWindowsAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        
        results = []
        for item in data.get("vulnerabilities", []):
            cve = item.get("cve", {})
            desc_list = cve.get("descriptions", [])
            desc = next((d["value"] for d in desc_list if d["lang"] == "en"), "No description")
            metrics = cve.get("metrics", {})
            cvss_v3 = metrics.get("cvssMetricV31", metrics.get("cvssMetricV30", [{}]))
            score_data = cvss_v3[0].get("cvssData", {}) if cvss_v3 else {}
            
            results.append({
                "id": cve.get("id"),
                "published": cve.get("published", "")[:10],
                "description": desc[:300] + ("..." if len(desc) > 300 else ""),
                "cvss_score": score_data.get("baseScore"),
                "severity": score_data.get("baseSeverity"),
                "vector": score_data.get("vectorString"),
            })
        
        return {
            "success": True,
            "keyword": keyword,
            "total_found": data.get("totalResults", 0),
            "results": results
        }
    except urllib.error.URLError as e:
        return {"success": False, "keyword": keyword, "error": f"Network error: {e}"}
    except Exception as e:
        return {"success": False, "keyword": keyword, "error": str(e)}


def system_info() -> dict:
    """
    Gather comprehensive local Windows system information using PowerShell.
    
    Returns:
        Dict with OS, hardware, network adapters, running services, and security info.
    """
    ps_cmd = """
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime, FreePhysicalMemory, TotalVisibleMemorySize
$cpu = Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed
$adapters = Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object Name, MacAddress, LinkSpeed, InterfaceDescription
$ips = Get-NetIPAddress | Where-Object AddressFamily -eq 'IPv4' | Select-Object InterfaceAlias, IPAddress, PrefixLength
$uac = (Get-ItemProperty HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System).EnableLUA
$av = Get-MpComputerStatus | Select-Object AMRunningMode, RealTimeProtectionEnabled, AntivirusEnabled, AntispywareEnabled -ErrorAction SilentlyContinue

@{
    OS = $os
    CPU = $cpu
    NetworkAdapters = $adapters
    IPAddresses = $ips
    UACEnabled = $uac
    AntivirusStatus = $av
} | ConvertTo-Json -Depth 4
"""
    result = _run_ps(ps_cmd, timeout=30)
    if result.get("success") and result.get("stdout"):
        try:
            data = json.loads(result["stdout"])
            return {"success": True, "system": data}
        except json.JSONDecodeError:
            return {"success": True, "raw": result["stdout"]}
    return {"success": False, "error": result.get("error") or result.get("stderr", "System info failed")}
