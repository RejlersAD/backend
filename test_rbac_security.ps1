# ============================================================================
# RBAC Security Testing Script
# Purpose: Verify that Default role users are blocked from sensitive endpoints
# ============================================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$JwtToken,
    
    [Parameter(Mandatory=$false)]
    [string]$BaseUrl = "https://www.radai.ae"
)

Write-Host "`n============================================================================" -ForegroundColor Cyan
Write-Host " RBAC SECURITY TEST - Verifying Module-Level Access Control" -ForegroundColor Cyan
Write-Host "============================================================================`n" -ForegroundColor Cyan

$headers = @{
    "Authorization" = "Bearer $JwtToken"
    "Content-Type" = "application/json"
}

# Test results
$results = @()

function Test-Endpoint {
    param(
        [string]$Name,
        [string]$Url,
        [string]$ExpectedResult,  # "BLOCKED" or "ALLOWED"
        [string]$Module
    )
    
    Write-Host "Testing: $Name" -ForegroundColor Yellow
    Write-Host "  URL: $Url" -ForegroundColor Gray
    Write-Host "  Module: $Module" -ForegroundColor Gray
    Write-Host "  Expected: $ExpectedResult" -ForegroundColor Gray
    
    try {
        $response = Invoke-WebRequest -Uri $Url -Headers $headers -Method GET -ErrorAction Stop
        $status = $response.StatusCode
        $actual = "ALLOWED"
        
        if ($ExpectedResult -eq "BLOCKED") {
            Write-Host "  Result: ❌ FAIL - Expected 403, got $status" -ForegroundColor Red
            $testResult = "FAIL"
        } else {
            Write-Host "  Result: ✅ PASS - Allowed as expected ($status)" -ForegroundColor Green
            $testResult = "PASS"
        }
    }
    catch {
        $status = $_.Exception.Response.StatusCode.value__
        
        if ($status -eq 403) {
            $actual = "BLOCKED"
            if ($ExpectedResult -eq "BLOCKED") {
                Write-Host "  Result: ✅ PASS - Blocked as expected (403)" -ForegroundColor Green
                $testResult = "PASS"
            } else {
                Write-Host "  Result: ❌ FAIL - Expected 200, got 403" -ForegroundColor Red
                $testResult = "FAIL"
            }
        }
        elseif ($status -eq 401) {
            Write-Host "  Result: ⚠️ WARN - Unauthorized (401) - Token may be invalid/expired" -ForegroundColor Yellow
            $testResult = "WARN"
            $actual = "UNAUTHORIZED"
        }
        else {
            Write-Host "  Result: ❌ ERROR - Unexpected status: $status" -ForegroundColor Red
            Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
            $testResult = "ERROR"
            $actual = "ERROR"
        }
    }
    
    Write-Host ""
    
    return [PSCustomObject]@{
        Name = $Name
        Module = $Module
        Expected = $ExpectedResult
        Actual = $actual
        Status = $status
        Result = $testResult
    }
}

Write-Host "🔐 SENSITIVE ENDPOINTS (Should be BLOCKED for Default Role)`n" -ForegroundColor Magenta

# Test Payroll endpoints (should be BLOCKED)
$results += Test-Endpoint -Name "Payroll Dashboard" `
    -Url "$BaseUrl/api/v1/payroll/dashboard-summary/" `
    -ExpectedResult "BLOCKED" `
    -Module "payroll"

$results += Test-Endpoint -Name "Salary History" `
    -Url "$BaseUrl/api/v1/payroll/salary-history/" `
    -ExpectedResult "BLOCKED" `
    -Module "payroll"

$results += Test-Endpoint -Name "Salary Components" `
    -Url "$BaseUrl/api/v1/payroll/salary-components/" `
    -ExpectedResult "BLOCKED" `
    -Module "payroll"

# Test Finance endpoints (should be BLOCKED)
$results += Test-Endpoint -Name "Finance Invoices" `
    -Url "$BaseUrl/api/v1/finance/invoices/" `
    -ExpectedResult "BLOCKED" `
    -Module "finance"

# Test Procurement endpoints (should be BLOCKED)
$results += Test-Endpoint -Name "Procurement Vendors" `
    -Url "$BaseUrl/api/v1/procurement/vendors/" `
    -ExpectedResult "BLOCKED" `
    -Module "procurement_vendors"

$results += Test-Endpoint -Name "Purchase Orders" `
    -Url "$BaseUrl/api/v1/procurement/purchase-orders/" `
    -ExpectedResult "BLOCKED" `
    -Module "procurement_orders"

# Test Sales endpoints (should be BLOCKED)
$results += Test-Endpoint -Name "Sales Clients" `
    -Url "$BaseUrl/api/v1/sales/clients/" `
    -ExpectedResult "BLOCKED" `
    -Module "sales"

Write-Host "`n✅ ENGINEERING ENDPOINTS (Should be ALLOWED for Default Role)`n" -ForegroundColor Green

# Test engineering endpoints (should be ALLOWED)
$results += Test-Endpoint -Name "PID Analysis" `
    -Url "$BaseUrl/api/v1/pid-analysis/" `
    -ExpectedResult "ALLOWED" `
    -Module "pid_verification"

$results += Test-Endpoint -Name "PFD Quality" `
    -Url "$BaseUrl/api/v1/pfd-quality/" `
    -ExpectedResult "ALLOWED" `
    -Module "pfd_quality"

# Test self-service endpoints (should be ALLOWED)
$results += Test-Endpoint -Name "Leave Requests (Self-Service)" `
    -Url "$BaseUrl/api/v1/payroll/leave-requests/" `
    -ExpectedResult "ALLOWED" `
    -Module "hr_self_service"

# Summary
Write-Host "`n============================================================================" -ForegroundColor Cyan
Write-Host " TEST SUMMARY" -ForegroundColor Cyan
Write-Host "============================================================================`n" -ForegroundColor Cyan

$totalTests = $results.Count
$passed = ($results | Where-Object { $_.Result -eq "PASS" }).Count
$failed = ($results | Where-Object { $_.Result -eq "FAIL" }).Count
$warnings = ($results | Where-Object { $_.Result -eq "WARN" }).Count
$errors = ($results | Where-Object { $_.Result -eq "ERROR" }).Count

Write-Host "Total Tests: $totalTests" -ForegroundColor White
Write-Host "Passed: $passed" -ForegroundColor Green
Write-Host "Failed: $failed" -ForegroundColor Red
Write-Host "Warnings: $warnings" -ForegroundColor Yellow
Write-Host "Errors: $errors" -ForegroundColor Red

if ($warnings -gt 0) {
    Write-Host "`n⚠️ WARNINGS DETECTED:" -ForegroundColor Yellow
    Write-Host "  - If you see 401 Unauthorized, your JWT token may have expired" -ForegroundColor Yellow
    Write-Host "  - Login to https://www.radai.ae again and get a fresh token" -ForegroundColor Yellow
}

if ($failed -gt 0) {
    Write-Host "`n❌ TEST FAILURES:" -ForegroundColor Red
    $results | Where-Object { $_.Result -eq "FAIL" } | ForEach-Object {
        Write-Host "  - $($_.Name): Expected $($_.Expected), got $($_.Actual)" -ForegroundColor Red
    }
}

if ($passed -eq $totalTests) {
    Write-Host "`n✅ ALL TESTS PASSED - RBAC Security is working correctly!" -ForegroundColor Green
}
elseif ($failed -eq 0 -and $warnings -gt 0) {
    Write-Host "`n⚠️ Tests completed with warnings - Check token validity" -ForegroundColor Yellow
}
else {
    Write-Host "`n❌ Some tests failed - Review security configuration" -ForegroundColor Red
}

# Export results
$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$reportPath = ".\RBAC_Test_Results_$timestamp.csv"
$results | Export-Csv -Path $reportPath -NoTypeInformation

Write-Host "`n📄 Detailed results saved to: $reportPath`n" -ForegroundColor Cyan

# Display results table
Write-Host "DETAILED RESULTS:" -ForegroundColor Cyan
$results | Format-Table -AutoSize

Write-Host "============================================================================`n" -ForegroundColor Cyan
