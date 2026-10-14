$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $health = Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 2
    if ($health.status -eq 'ok' -and $health.framework -eq 'agentscope') {
        Write-Host 'Night Council is already running.'
        Start-Process 'http://127.0.0.1:8765'
        exit 0
    }
} catch { }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python 3.12 .venv
    } else {
        py -3.12 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
$dependencyProbe = "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(x) for x in 'agentscope fastapi uvicorn openai'.split()) else 1)"
& '.venv\Scripts\python.exe' -c $dependencyProbe
if ($LASTEXITCODE -ne 0) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv pip install --python '.venv\Scripts\python.exe' -r requirements.lock.txt
    } else {
        & '.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
    }
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
Write-Host 'Night Council: http://127.0.0.1:8765'
Write-Host 'Keep this window open while playing. Ctrl+C stops the server.'
Start-Process 'http://127.0.0.1:8765'
& '.venv\Scripts\python.exe' run.py
