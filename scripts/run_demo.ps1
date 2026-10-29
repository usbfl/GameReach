$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $ProjectRoot 'backend')
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv sync --frozen --no-dev
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network and Python version.' }
} else {
    if (!(Test-Path '.venv\Scripts\python.exe')) {
        if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv }
        else { python -m venv .venv }
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or newer, then retry.' }
    }
    & '.venv\Scripts\python.exe' -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
    & '.venv\Scripts\python.exe' -c 'import fastapi, uvicorn, sqlalchemy, alembic, httpx, pydantic_settings, bs4, socksio' 2>$null
    if ($LASTEXITCODE -ne 0) {
        & '.venv\Scripts\python.exe' -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network access.' }
    }
}
if (!(Test-Path (Join-Path $ProjectRoot 'frontend\dist\index.html'))) { throw 'Run cd frontend; npm ci; npm run build first.' }
& '.venv\Scripts\python.exe' -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
Write-Host 'GameReach: http://127.0.0.1:8000  (Ctrl+C to stop)' -ForegroundColor Green
& '.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
