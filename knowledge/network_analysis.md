# Network Packet Analysis & Wireshark / TShark Reference

This reference covers the detection, capture, and offline analysis of network traffic on Windows systems using native tools and standard Wireshark/TShark utilities.

## Finding Wireshark and TShark on Windows
Wireshark is typically installed in default location paths. To programmatically locate it via PowerShell:
```powershell
$paths = @(
    "$env:ProgramFiles\Wireshark\tshark.exe",
    "${env:ProgramFiles(x86)}\Wireshark\tshark.exe",
    "$env:SystemDrive\Program Files\Wireshark\tshark.exe"
)
$tshark = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
```

## Listing Available Network Capture Interfaces
To list all available capture adapters in a human-readable format:
```cmd
"C:\Program Files\Wireshark\tshark.exe" -D
```

## Running Live Network Captures
Always run captures with a strict time duration or packet count limit to avoid filling the disk or exhausting system resources.

### Capturing traffic on Interface Index 1 for 10 seconds:
```cmd
"C:\Program Files\Wireshark\tshark.exe" -i 1 -a duration:10 -w capture_file.pcapng
```

### Capturing specific port traffic (e.g., HTTP/HTTPS):
```cmd
"C:\Program Files\Wireshark\tshark.exe" -i 1 -f "tcp port 80 or tcp port 443" -a duration:15 -w web_capture.pcapng
```

## PCAPNG / PCAP File Analysis
Offline analysis of captured packet files to find protocols, conversations, and plaintext credentials.

### Displaying Protocol Hierarchy Statistics:
```cmd
"C:\Program Files\Wireshark\tshark.exe" -r capture_file.pcapng -z io,phs
```

### Finding IP Conversations (Src/Dst Address pairs and byte volumes):
```cmd
"C:\Program Files\Wireshark\tshark.exe" -r capture_file.pcapng -z conv,ip
```

### Extracting HTTP GET requests and hosts:
```cmd
"C:\Program Files\Wireshark\tshark.exe" -r capture_file.pcapng -Y "http.request.method == GET" -T fields -e http.host -e http.request.uri
```

### Scanning for plaintext credentials (HTTP Basic Auth, FTP, SMTP, POP3):
```cmd
"C:\Program Files\Wireshark\tshark.exe" -r capture_file.pcapng -Y "http.authbasic or ftp or smtp or pop" -T fields -e ip.src -e ip.dst -e text
```
