#!/usr/bin/env python3
"""
create_shortcut.py  —  Windows only
Creates a TradingAgents desktop shortcut.
Run once: python create_shortcut.py
"""
import ctypes
import subprocess
from ctypes import wintypes
from pathlib import Path


def get_desktop() -> Path:
    """Resolve Desktop path — handles OneDrive redirect correctly."""
    CSIDL_DESKTOP = 0
    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, 0, buf)
    return Path(buf.value)


def main():
    desktop    = get_desktop()
    script_dir = Path(__file__).parent.resolve()
    lnk        = desktop / 'TradingAgents.lnk'
    bat        = script_dir / 'run.bat'
    python_exe = script_dir / '.venv' / 'Scripts' / 'python.exe'

    # Icon: use Python from .venv (always present after setup); fall back to cmd.exe
    icon = f'{python_exe},0' if python_exe.exists() else r'%SystemRoot%\System32\cmd.exe,0'

    # Build shortcut via PowerShell WScript.Shell (no extra Python deps needed)
    ps = (
        '$ws = New-Object -ComObject WScript.Shell; '
        f'$sc = $ws.CreateShortcut("{lnk}"); '
        '$sc.TargetPath = "cmd.exe"; '
        f'$sc.Arguments = \'/c ""{bat}""\'; '
        f'$sc.WorkingDirectory = "{script_dir}"; '
        '$sc.Description = "TradingAgents - AI Multi-Agent Financial Analysis"; '
        f'$sc.IconLocation = "{icon}"; '
        '$sc.WindowStyle = 1; '
        '$sc.Save()'
    )
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True)

    print(f'Shortcut created : {lnk}')
    print('Double-click TradingAgents on your Desktop to launch.')
    print()
    print('Tip: right-click the shortcut -> Properties -> Change Icon')
    print('     to pick a different icon if you prefer.')


if __name__ == '__main__':
    main()
