$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host "Sidd Trading Agent auto-update started. Checking GitHub every 60 seconds." -ForegroundColor Cyan
Write-Host "Keep this terminal open. Press Ctrl+C to stop." -ForegroundColor Yellow

while ($true) {
    try {
        git fetch origin main
        if ($LASTEXITCODE -ne 0) {
            Write-Host "GitHub fetch failed; will retry in 60 seconds." -ForegroundColor Red
        }
        else {
            $status = git status --porcelain
            if ($status) {
                Write-Host "Local changes detected. Skipping pull to protect your work." -ForegroundColor Yellow
            }
            else {
                $local = git rev-parse HEAD
                $remote = git rev-parse origin/main

                if ($LASTEXITCODE -eq 0 -and $local -ne $remote) {
                    git pull --ff-only origin main
                    if ($LASTEXITCODE -eq 0) {
                        Write-Host "Updated local files from GitHub." -ForegroundColor Green
                    }
                    else {
                        Write-Host "Pull could not be applied safely; no forced changes were made." -ForegroundColor Red
                    }
                }
            }
        }
    }
    catch {
        Write-Host "Update check error: $_" -ForegroundColor Red
    }

    Start-Sleep -Seconds 60
}
