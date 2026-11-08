# Run the browser tests on a Windows machine whose PostgreSQL, API and worker live in WSL.
#   powershell -ExecutionPolicy Bypass -File scripts/e2e-local.ps1 [playwright args, e.g. -g "invoice"]
# It resets a separate database (biz360x_e2e), starts the API and worker in WSL, starts the web app here, runs the tests, then stops everything.
$ErrorActionPreference = "Continue"   # native commands print notices on stderr; failures are checked by exit code
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$wslRoot = (wsl wslpath -a ($root -replace '\\', '/')).Trim()

function Stop-All {
  wsl bash "$wslRoot/scripts/e2e-stack.sh" stop 2>$null | Out-Null
  foreach ($c in Get-NetTCPConnection -LocalPort 3100 -State Listen -ErrorAction SilentlyContinue) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
}

try {
  Stop-All
  Write-Host "Starting the database, API and worker in WSL..."
  wsl bash "$wslRoot/scripts/e2e-stack.sh" start
  if ($LASTEXITCODE -ne 0) { throw "The API did not start" }

  $ip = ((wsl hostname -I) -split ' ')[0]
  $env:API_URL = "http://${ip}:8000"
  Remove-Item -Recurse -Force "$root\web\.next" -ErrorAction SilentlyContinue
  Write-Host "Building and starting the web app on :3100 (API at $env:API_URL)..."
  Push-Location "$root\web"
  npx.cmd next build | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "next build failed" }
  Start-Process cmd -ArgumentList '/c', 'npx.cmd next start -p 3100 > ..\var\e2e-web.log 2>&1' -WindowStyle Hidden
  for ($i = 0; $i -lt 60; $i++) { try { if ((Invoke-WebRequest http://localhost:3100/login -UseBasicParsing).StatusCode -eq 200) { break } } catch { Start-Sleep 1 } }

  $env:E2E_BASE_URL = "http://localhost:3100"
  npx.cmd playwright test @args
  $code = $LASTEXITCODE
  Pop-Location
} finally {
  Stop-All
}
exit $code
