# Upload local cultivation_bot.sqlite3 directly to Railway volume (/data).
# Requires: npm i -g @railway/cli, railway login, railway link

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$dbName = "cultivation_bot.sqlite3"
$remotePath = "/data/cultivation_bot.sqlite3"

if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*DATABASE_PATH\s*=\s*"?([^"]+)"?\s*$') {
            $dbName = $Matches[1].Trim()
            if ($dbName -match '^/') {
                $remotePath = $dbName
            }
            break
        }
    }
}

$source = Join-Path $root $dbName
if (-not (Test-Path $source)) {
    Write-Error "Local database not found: $source"
}

$railway = Get-Command railway -ErrorAction SilentlyContinue
if (-not $railway) {
    Write-Error "Railway CLI not found. Install: npm i -g @railway/cli"
}

Write-Host "Uploading $source ($((Get-Item $source).Length) bytes) via transfer.sh ..."
$upload = curl.exe -s --upload-file $source https://transfer.sh/$dbName
if (-not $upload -or $upload -notmatch '^https://') {
    Write-Error "transfer.sh upload failed: $upload"
}
$url = ($upload -split "`n")[0].Trim()
Write-Host "Temporary URL: $url"
Write-Host "Downloading into Railway volume at $remotePath ..."

$py = @"
import urllib.request
urllib.request.urlretrieve('$url', '$remotePath')
import os, sqlite3
print('bytes', os.path.getsize('$remotePath'))
c = sqlite3.connect('$remotePath')
print('players', c.execute('select count(*) from players').fetchone()[0])
c.close()
"@

railway run -- python -c $py
Write-Host "Done. Restart the bot service on Railway if it was running."
