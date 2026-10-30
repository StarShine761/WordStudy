Set-ExecutionPolicy RemoteSigned -Scope Process -Force
# UTF-8 console so Chinese and emoji display correctly
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 > $null

# Enable Virtual Terminal Processing so ANSI color codes (e.g. ESC[1;32m) render as colors
try {
    if (-not ('Win32.K32' -as [type])) {
        $sig = '[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n); [DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m); [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);'
        Add-Type -MemberDefinition $sig -Name 'K32' -Namespace 'Win32' -ErrorAction SilentlyContinue
    }
    $h = [Win32.K32]::GetStdHandle(-11)
    $m = 0
    if ([Win32.K32]::GetConsoleMode($h, [ref]$m)) { [Win32.K32]::SetConsoleMode($h, $m -bor 0x0004) | Out-Null }
} catch {}

# Run Python in UTF-8 mode so emoji etc. output correctly
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Switch to script folder so .venv and word_study.py can be found
Set-Location $PSScriptRoot

# Narrow the console to the 60-column UI and center it; the default window is
# wide and keeps the text stuck at the left edge of the screen.
try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace WordStudy {
    public static class ConsoleLayout {
        [DllImport("kernel32.dll")]
        public static extern IntPtr GetConsoleWindow();
        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
        [DllImport("user32.dll")]
        public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")]
        public static extern IntPtr MonitorFromWindow(IntPtr hWnd, uint flags);
        [DllImport("user32.dll")]
        public static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO info);

        [StructLayout(LayoutKind.Sequential)]
        public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

        [StructLayout(LayoutKind.Sequential)]
        public struct MONITORINFO {
            public int cbSize;
            public RECT rcMonitor;
            public RECT rcWork;
            public uint dwFlags;
        }
    }
}
"@ -ErrorAction Stop
} catch {}

try {
    $hwnd = [WordStudy.ConsoleLayout]::GetConsoleWindow()
    if ($hwnd -ne [IntPtr]::Zero) {
        [WordStudy.ConsoleLayout]::ShowWindow($hwnd, 9) | Out-Null  # SW_RESTORE

        $cols = 72
        $rows = 34
        try {
            if ([Console]::WindowWidth -gt $cols) { [Console]::WindowWidth = $cols }
            if ([Console]::BufferWidth -gt $cols) { [Console]::BufferWidth = $cols }
            if ([Console]::WindowHeight -gt $rows) { [Console]::WindowHeight = $rows }
        } catch {}

        Start-Sleep -Milliseconds 120

        $rect = New-Object -TypeName 'WordStudy.ConsoleLayout+RECT'
        if ([WordStudy.ConsoleLayout]::GetWindowRect($hwnd, [ref]$rect)) {
            $width = $rect.Right - $rect.Left
            $height = $rect.Bottom - $rect.Top
            $monitor = [WordStudy.ConsoleLayout]::MonitorFromWindow($hwnd, 2)
            $info = New-Object -TypeName 'WordStudy.ConsoleLayout+MONITORINFO'
            $info.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($info)
            if ([WordStudy.ConsoleLayout]::GetMonitorInfo($monitor, [ref]$info)) {
                $work = $info.rcWork
                if ($work.Right -gt $work.Left -and $work.Bottom -gt $work.Top) {
                    $x = $work.Left + [Math]::Max(0, [int](($work.Right - $work.Left - $width) / 2))
                    $y = $work.Top + [Math]::Max(0, [int](($work.Bottom - $work.Top - $height) / 2))
                    [WordStudy.ConsoleLayout]::SetWindowPos($hwnd, [IntPtr]::Zero, $x, $y, 0, 0, 0x0005) | Out-Null
                }
            }
        }
    }
} catch {}

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[ERROR] Virtual environment .venv not found" -ForegroundColor Red
    Write-Host "Run these first:" -ForegroundColor Yellow
    Write-Host "    python -m venv .venv"
    Write-Host "    .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    Read-Host "Press Enter to exit"
    exit 1
}

& $py word_study.py
Read-Host "Press Enter to exit"
