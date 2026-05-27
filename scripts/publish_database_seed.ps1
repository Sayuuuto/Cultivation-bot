# Copy your local SQLite DB into deploy/seed/ so Railway can load it on first boot.
# The seed is the only *.sqlite3 file allowed in git (see .gitignore).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$dbName = "cultivation_bot.sqlite3"

if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*DATABASE_PATH\s*=\s*"?([^"]+)"?\s*$') {
            $dbName = $Matches[1].Trim()
            break
        }
    }
}

$source = Join-Path $root $dbName
$destDir = Join-Path $root "deploy\seed"
$dest = Join-Path $destDir $dbName

if (-not (Test-Path $source)) {
    Write-Error "Local database not found: $source"
}

New-Item -ItemType Directory -Force -Path $destDir | Out-Null
Copy-Item -Force $source $dest
$bytes = (Get-Item $dest).Length
Write-Host "Copied $bytes bytes to deploy/seed/$dbName"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  git add deploy/seed/cultivation_bot.sqlite3"
Write-Host "  git commit -m 'Add production database seed'"
Write-Host "  git push"
Write-Host ""
Write-Host "On Railway: ensure volume is mounted at /data, then redeploy."
Write-Host "At startup the bot copies deploy/seed/$dbName into DATABASE_PATH when the volume has 0 players."
Write-Host "If the volume already has players, delete the volume or use upload_database_to_railway.ps1."
