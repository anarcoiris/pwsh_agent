"""Tests for try_http_login PowerShell script generation and launcher."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.powershell_exec import powershell_executable
from tools.recon import _try_http_login_script


def test_script_avoids_secure_string_module():
    cfg = {
        "url": "http://192.168.1.1",
        "user": "user",
        "password": 'Qtpowppu27"/',
        "method": "auto",
        "uField": "username",
        "pField": "password",
        "timeout": 15,
    }
    script = _try_http_login_script(cfg)
    assert "ConvertTo-SecureString" not in script
    assert "PSCredential" not in script
    assert "Authorization" in script
    assert "FromBase64String" in script


def test_powershell_exe_prefers_winps51_on_windows():
    if sys.platform != "win32":
        return
    exe = powershell_executable().lower()
    assert "windowspowershell" in exe or exe.endswith("powershell.exe")
