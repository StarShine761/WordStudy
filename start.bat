@echo off
REM Prefer PowerShell 7 (pwsh.exe) - native UTF-8 + VT color support.
REM Fall back to Windows PowerShell 5.1 if pwsh is not installed.
if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe" (
    start "" "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe" -ExecutionPolicy RemoteSigned -File "%~dp0start.ps1"
) else (
    start powershell.exe -ExecutionPolicy RemoteSigned -File "%~dp0start.ps1"
)
