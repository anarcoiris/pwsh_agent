"""
tools/__init__.py

Re-exports all tools (legacy + new) as a single unified namespace.
The agent imports `import tools` and gets everything from here.
"""

# ── Legacy tools (from root tools.py via sys path) ─────────────────────────
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import legacy monolithic tools.py (still lives at root level)
# We do a selective import to avoid circular issues
import importlib
_legacy = importlib.import_module("tools_legacy")

SequentialThinkingEngine = _legacy.SequentialThinkingEngine
host_exec               = _legacy.host_exec
run_script              = _legacy.run_script
read_file               = _legacy.read_file
write_file              = _legacy.write_file
append_note             = _legacy.append_note
find_file               = _legacy.find_file
grep_file               = _legacy.grep_file
find_and_grep           = _legacy.find_and_grep
list_network_interfaces = _legacy.list_network_interfaces
capture_packets         = _legacy.capture_packets
analyze_pcapng          = _legacy.analyze_pcapng
crack_hash              = _legacy.crack_hash
find_tshark             = _legacy.find_tshark
TOOLS_SCHEMA            = list(_legacy.TOOLS_SCHEMA)  # mutable copy we'll extend

# ── New Windows-native tools ────────────────────────────────────────────────
from tools.recon import (
    dns_lookup,
    ping_sweep,
    port_scan,
    http_headers_check,
    ssl_analysis,
    try_http_login,
    cve_lookup,
    system_info,
)
from tools.intel import (
    encode_decode,
    hash_identify,
    finding_create,
    finding_list,
    report_generate,
)

# ── Extend TOOLS_SCHEMA with new tool definitions ───────────────────────────
TOOLS_SCHEMA += [
    {
        "type": "function",
        "function": {
            "name": "dns_lookup",
            "description": "Resolve DNS records for a hostname using native PowerShell Resolve-DnsName.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hostname": {"type": "string", "description": "The target hostname or domain to resolve."},
                    "record_type": {"type": "string", "description": "DNS record type — A, AAAA, MX, NS, TXT, CNAME, SOA (default: A)."}
                },
                "required": ["hostname"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ping_sweep",
            "description": "Discover live hosts in a subnet using parallel PowerShell ping sweep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cidr": {"type": "string", "description": "Target network in CIDR notation (e.g., 192.168.1.0/24) or IP range like 192.168.1.1-50."},
                    "timeout_ms": {"type": "integer", "description": "Ping timeout in milliseconds (default: 500)."}
                },
                "required": ["cidr"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "port_scan",
            "description": "Scan TCP ports on a target using native PowerShell Test-NetConnection. Falls back to nmap for port ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "IP address or hostname to scan."},
                    "ports": {"type": "string", "description": "Comma-separated port list (e.g., '22,80,443') or range '1-1024'. Default: common ports."},
                    "timeout_ms": {"type": "integer", "description": "Connection timeout in milliseconds (default: 1000)."}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "http_headers_check",
            "description": "Fetch HTTP response headers and analyze security posture of a web endpoint (missing headers, server fingerprint, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to inspect (e.g., https://example.com)."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssl_analysis",
            "description": "Analyze the SSL/TLS certificate and configuration of a remote host (version, cipher, expiry, SANs, weak protocols).",
            "parameters": {
                "type": "object",
                "properties": {
                    "hostname": {"type": "string", "description": "Target hostname to connect to."},
                    "port": {"type": "integer", "description": "TLS port (default: 443)."}
                },
                "required": ["hostname"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "try_http_login",
            "description": "Attempt to authenticate to an HTTP endpoint with a username and password (HTTP Basic and/or form POST), returning a heuristic accepted/rejected verdict. Use this for 'try user X with password Y against <site>' — NOT hash_identify/crack_hash, since a known plaintext password is not a hash.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Target URL (e.g., http://192.168.1.1 or http://host/login)."},
                    "user": {"type": "string", "description": "Username to try."},
                    "password": {"type": "string", "description": "Password to try."},
                    "method": {"type": "string", "description": "'auto' (basic then form), 'basic', or 'form' (default: auto)."},
                    "username_field": {"type": "string", "description": "Form field name for the username (default: username)."},
                    "password_field": {"type": "string", "description": "Form field name for the password (default: password)."},
                    "timeout_sec": {"type": "integer", "description": "Per-request timeout in seconds (default: 15)."}
                },
                "required": ["url", "user", "password"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cve_lookup",
            "description": "Look up recent CVEs from the NIST NVD API by keyword or CVE ID. Returns severity, CVSS score, and descriptions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Search keyword (product name, CVE ID, or technology)."},
                    "max_results": {"type": "integer", "description": "Maximum number of results to return (default: 5)."}
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_info",
            "description": "Gather comprehensive local Windows system information: OS, CPU, network adapters, IP addresses, UAV status, antivirus state.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "encode_decode",
            "description": "Encode or decode text using common schemes: base64, base64url, hex, url, rot13, utf8_bytes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Input text to process."},
                    "operation": {"type": "string", "description": "'encode' or 'decode'."},
                    "encoding": {"type": "string", "description": "Scheme: base64 | base64url | hex | url | rot13 | utf8_bytes (default: base64)."}
                },
                "required": ["text", "operation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hash_identify",
            "description": "Identify the likely hash algorithm of a given hash string by pattern matching (MD5, SHA-1, SHA-256, bcrypt, Argon2, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "hash_value": {"type": "string", "description": "The hash string to analyze."}
                },
                "required": ["hash_value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finding_create",
            "description": "Create and persist a security finding to the local SQLite database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short descriptive title of the finding."},
                    "severity": {"type": "string", "description": "Severity: CRITICAL | HIGH | MEDIUM | LOW | INFO."},
                    "description": {"type": "string", "description": "Detailed description of what was found."},
                    "target": {"type": "string", "description": "Affected host, URL, or file path (optional)."},
                    "evidence": {"type": "string", "description": "Raw evidence snippet (output, log, etc.) (optional)."},
                    "recommendation": {"type": "string", "description": "Suggested remediation steps (optional)."},
                    "specialist": {"type": "string", "description": "Active specialist mode at time of finding (default: lead)."}
                },
                "required": ["title", "severity", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finding_list",
            "description": "List persisted security findings from the local database, optionally filtered by severity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity_filter": {"type": "string", "description": "Optional severity filter: CRITICAL | HIGH | MEDIUM | LOW | INFO."},
                    "limit": {"type": "integer", "description": "Maximum number of findings to return (default: 50)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "report_generate",
            "description": "Generate a structured Markdown engagement report from all findings in the local database, sorted by severity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_format": {"type": "string", "description": "Output format: markdown | text (default: markdown)."},
                    "title": {"type": "string", "description": "Report title (default: 'Pulse Agent Engagement Report')."}
                }
            }
        }
    },
]

__all__ = [
    "SequentialThinkingEngine",
    "host_exec", "run_script", "read_file", "write_file", "append_note", "find_file", "grep_file", "find_and_grep",
    "list_network_interfaces", "capture_packets", "analyze_pcapng",
    "crack_hash", "find_tshark",
    "dns_lookup", "ping_sweep", "port_scan",
    "http_headers_check", "ssl_analysis", "try_http_login", "cve_lookup", "system_info",
    "encode_decode", "hash_identify",
    "finding_create", "finding_list", "report_generate",
    "TOOLS_SCHEMA",
]
