# load-env.ps1
# Loads environment variables from a .env file into the current PowerShell session.
#
# Usage:
#   .\load-env.ps1                 # loads .env from current directory
#   .\load-env.ps1 -Path .env.prod # loads a specific file

param(
    [string]$Path = ".env"
)

if (-not (Test-Path $Path)) {
    Write-Host "File not found: $Path" -ForegroundColor Red
    return
}

$count = 0

Get-Content $Path | ForEach-Object {
    $line = $_.Trim()

    # skip blank lines and comments
    if ($line -eq "" -or $line.StartsWith("#")) {
        return
    }

    if ($line -match '^\s*([^=]+?)\s*=\s*(.*)$') {
        $name  = $matches[1].Trim()
        $value = $matches[2].Trim()

        # strip surrounding quotes if present
        if ($value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        elseif ($value.StartsWith("'") -and $value.EndsWith("'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [System.Environment]::SetEnvironmentVariable($name, $value, 'Process')
        $count++
    }
}

Write-Host "Loaded $count environment variable(s) from $Path" -ForegroundColor Green