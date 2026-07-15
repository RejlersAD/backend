# PowerShell script to run production fix via Railway
# Handles output encoding issues on Windows

$ErrorActionPreference = "Continue"

Write-Host "========================================"
Write-Host "Running Production Fix via Railway..."
Write-Host "========================================"

# Change to backend directory
Set-Location "c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend"

# Run the fix script
Write-Host "`nExecuting: railway run python production_complete_fix.py`n"

try {
    # Run command and capture output
    $output = railway run python production_complete_fix.py 2>&1
    
    # Display output
    $output | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            Write-Host $_.Exception.Message -ForegroundColor Yellow
        } else {
            Write-Host $_
        }
    }
    
    Write-Host "`n========================================"
    Write-Host "Fix command completed!"
    Write-Host "========================================"
    
    # Now verify with status check
    Write-Host "`nRunning verification check...`n"
    $verify = railway run -- python manage.py check_procurement_status 2>&1
    $verify | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            Write-Host $_.Exception.Message -ForegroundColor Yellow
        } else {
            Write-Host $_
        }
    }
    
} catch {
    Write-Host "`nError: $_" -ForegroundColor Red
    Write-Host "`nTrying alternative method...`n" -ForegroundColor Yellow
    
    # Alternative: Use management command
    railway run -- python manage.py fix_production_procurement --seed
}

Write-Host "`n========================================"
Write-Host "NEXT STEP:"
Write-Host "Open https://www.radai.ae/procurement/orders"
Write-Host "and verify data appears!"
Write-Host "========================================"
