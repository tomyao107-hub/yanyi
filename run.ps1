$ErrorActionPreference = "Stop"
$repoPath = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [int] $ProcessId
    )

    $children = @(
        Get-CimInstance Win32_Process `
            -Filter "ParentProcessId = $ProcessId" `
            -ErrorAction SilentlyContinue
    )

    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId $child.ProcessId
    }

    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath "$repoPath\.venv")) {
    python -m venv "$repoPath\.venv"
}

Push-Location $repoPath
try {
    & "$repoPath\.venv\Scripts\python.exe" -m pip install -e "$repoPath"
    & "$repoPath\.venv\Scripts\python.exe" -m alembic -c "$repoPath\backend\alembic.ini" upgrade head
}
finally {
    Pop-Location
}

$api = Start-Process `
    -FilePath "$repoPath\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $repoPath `
    -WindowStyle Hidden `
    -PassThru

try {
    Push-Location "$repoPath\frontend"
    if (-not (Test-Path -LiteralPath "node_modules")) {
        npm.cmd install
    }
    npm.cmd run dev
}
finally {
    Pop-Location
    Stop-ProcessTree -ProcessId $api.Id
}
