---
tools: [host_exec, read_file]
phase: [re]
---

# Reverse Engineering & Binary Inspection Reference

Guidelines, utilities, and script strategies for reverse engineering, static inspection, and dynamic debugging of Windows PE binaries natively.

## Static Inspection

### CertUtil MD5 / SHA-256 Hash Matching
Use the native `CertUtil` tool or PowerShell cmdlets to calculate cryptographic hashes for security auditing:
```powershell
Get-FileHash -Path "target.exe" -Algorithm SHA256
```
```cmd
certutil -hashfile target.exe SHA256
```

### Strings Extraction
If Sysinternals `strings.exe` is not installed, extraction can be approximated in pure PowerShell:
```powershell
function Get-Strings {
    param([string]$Path, [int]$MinLength = 4)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $str = ""
    foreach ($b in $bytes) {
        if ($b -ge 32 -and $b -le 126) {
            $str += [char]$b
        } else {
            if ($str.Length -ge $MinLength) {
                $str
            }
            $str = ""
        }
    }
}
```

### PE Header and Dependency Auditing
To inspect imported DLLs and function exports without GUI tools, use the `.NET` assembly parser or search raw bytes for names matching common libraries (e.g., `kernel32.dll`, `advapi32.dll`, `ws2_32.dll`).

## Dynamic Inspection and Sandbox Execution
When executing an untrusted binary, ensure safety:
1. **Network Safety Mode**: Verify the agent's Security Badge matches `SANDBOX` or similar constraint profiles before launching processes.
2. **Process Auditing**: Monitor child processes spawned by a dynamic binary.
```powershell
Get-CimInstance -ClassName Win32_Process -Filter "ParentProcessId = $targetPID"
```

## Binary Obfuscation and Decoding
Many binaries hide payloads inside custom-encoded string variables. Common encoding mechanisms include:
- **Base64 / Base64URL**: Decoded via `[System.Convert]::FromBase64String($encoded)`
- **ROT13**: Caesar cipher shift by 13 characters
- **Hex Encoded Payload Strings**: Parsed back into byte arrays
- **XOR Encrypted Blocks**: Decrypted using simple single-byte or multi-byte keys:
```powershell
function Decrypt-Xor {
    param([byte[]]$Data, [byte]$Key)
    $out = New-Object byte[] $Data.Length
    for ($i=0; $i -lt $Data.Length; $i++) {
        $out[$i] = $Data[$i] -bxor $Key
    }
    [System.Text.Encoding]::UTF8.GetString($out)
}
```
