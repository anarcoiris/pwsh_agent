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
