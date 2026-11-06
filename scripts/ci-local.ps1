# Run the CI mirror inside WSL (PostgreSQL 16 + Python + node live there).
# Usage: powershell -ExecutionPolicy Bypass -File scripts/ci-local.ps1 [--skip-lint] [--keep-db] [pytest args]
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$wslPath = (wsl wslpath -a $repo).Trim()
# start PostgreSQL if it is not running (needs root; the default WSL user may be prompted)
wsl -u root -- bash -lc "service postgresql status >/dev/null 2>&1 || service postgresql start" | Out-Null
wsl -- bash -lc "cd '$wslPath' && bash scripts/ci-local.sh $($args -join ' ')"
exit $LASTEXITCODE
