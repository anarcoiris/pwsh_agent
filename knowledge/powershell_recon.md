---
tools: [dns_lookup, ping_sweep, port_scan, host_exec, system_info]
phase: [recon]
---

# PowerShell Reconnaissance & Local Discovery Reference

This document provides a set of highly optimized, native PowerShell command patterns and logic for performing local system discovery, active reconnaissance, and information gathering on Windows hosts.

## Active Subnet Discovery (Ping Sweep)
Instead of relying on external binaries like `nmap` or `ping`, use parallelized native `.NET` namespaces via PowerShell to scan subnets quickly.

### parallelized ping sweep snippet:
```powershell
$Subnet = "192.168.1"
1..254 | ForEach-Object -Parallel {
    $ip = "$using:Subnet.$_"
    $ping = New-Object System.Net.NetworkInformation.Ping
    $res = $ping.Send($ip, 200)
    if ($res.Status -eq "Success") {
        [PSCustomObject]@{
            IP = $ip
            Hostname = [System.Net.Dns]::GetHostEntry($ip).HostName
        }
    }
} -ThrottleLimit 50
```

## Local Port Scanning (Test-NetConnection)
On modern Windows platforms, `Test-NetConnection` is the preferred cmdlet for checking TCP port connectivity.

### Basic port check:
```powershell
Test-NetConnection -ComputerName "127.0.0.1" -Port 443
```

### Quick scan of common TCP ports:
```powershell
$ports = @(21, 22, 23, 25, 53, 80, 135, 139, 443, 445, 1433, 3306, 3389, 8080)
$ports | ForEach-Object -Parallel {
    $t = Test-NetConnection -ComputerName "127.0.0.1" -Port $_ -WarningAction SilentlyContinue
    if ($t.TcpTestSucceeded) {
        [PSCustomObject]@{ Port = $_; Status = "Open" }
    }
}
```

## WMI & CIM System Information Gathering
Windows Management Instrumentation (WMI) and the newer Common Information Model (CIM) cmdlets are extremely powerful for retrieving system hardware, software, and configuration details.

### OS and Architecture:
```powershell
Get-CimInstance -ClassName Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture, CSName
```

### Local Network Configuration:
```powershell
Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True" | Select-Object Description, IPAddress, MACAddress, DNSDomain, DNSServerSearchOrder
```

### UAV / UAC Status check:
```powershell
Get-ItemProperty -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System" | Select-Object EnableLUA, ConsentPromptBehaviorAdmin
```

### Antivirus / Security Product Detection:
```powershell
Get-CimInstance -Namespace "root\SecurityCenter2" -ClassName "AntiVirusProduct" | Select-Object displayName, productState, pathToSignedProductExe
```
