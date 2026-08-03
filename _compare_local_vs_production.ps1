# Spec Customization Configuration Comparison
# ============================================
# Compares extraction configuration between local development and production
# to diagnose why extraction results differ (e.g., 1858 vs 116 records).

$ErrorActionPreference = "Continue"

Write-Host "`n" -NoNewline
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host "  Spec Customization Configuration Comparison Tool" -ForegroundColor Cyan  
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host ""

# Configuration
$localUrl = "http://localhost:8000/api/v1/spec-customization/diagnostics/config/"
$productionUrl = "https://aiflowbackend-production.up.railway.app/api/v1/spec-customization/diagnostics/config/"

# Authentication (you need to provide valid tokens)
Write-Host "📝 Authentication Required" -ForegroundColor Yellow
Write-Host "   Please provide authentication tokens for both environments.`n"

# For local, try to get token (you may need to adjust this)
$localUsername = Read-Host "Local username (or press Enter to skip)"
if ($localUsername) {
    $localPassword = Read-Host "Local password" -AsSecureString
    $localPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($localPassword))
} else {
    Write-Host "   Skipping local authentication - will attempt unauthenticated request" -ForegroundColor Yellow
}

# For production
$prodUsername = Read-Host "`nProduction username (or press Enter to skip)"
if ($prodUsername) {
    $prodPassword = Read-Host "Production password" -AsSecureString
    $prodPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($prodPassword))
} else {
    Write-Host "   Skipping production authentication - will attempt unauthenticated request" -ForegroundColor Yellow
}

Write-Host ""

# Function to get auth token
function Get-AuthToken {
    param(
        [string]$BaseUrl,
        [string]$Username,
        [string]$Password
    )
    
    if (-not $Username -or -not $Password) {
        return $null
    }
    
    $tokenUrl = $BaseUrl -replace "/spec-customization.*", "/auth/login/"
    
    try {
        $response = Invoke-RestMethod -Uri $tokenUrl -Method Post -Body @{
            username = $Username
            password = $Password
        } -ContentType "application/json"
        
        return $response.access
    } catch {
        Write-Host "   ⚠️  Failed to get auth token: $_" -ForegroundColor Yellow
        return $null
    }
}

# Function to fetch configuration
function Get-Configuration {
    param(
        [string]$Url,
        [string]$Environment,
        [string]$Token
    )
    
    Write-Host "────────────────────────────────────────────────────────────────────────────" -ForegroundColor Gray
    Write-Host "📡 Fetching $Environment configuration..." -ForegroundColor Cyan
    Write-Host "   URL: $Url" -ForegroundColor Gray
    
    try {
        $headers = @{}
        if ($Token) {
            $headers["Authorization"] = "Bearer $Token"
        }
        
        $config = Invoke-RestMethod -Uri $Url -Method Get -Headers $headers
        
        Write-Host "   ✅ Success" -ForegroundColor Green
        return $config
    } catch {
        Write-Host "   ❌ Failed: $($_.Exception.Message)" -ForegroundColor Red
        
        # If 401, might need auth
        if ($_.Exception.Response.StatusCode -eq 401) {
            Write-Host "   💡 Authentication required - please provide valid credentials" -ForegroundColor Yellow
        }
        
        return $null
    }
}

# Function to print configuration
function Print-Configuration {
    param(
        [object]$Config,
        [string]$Environment
    )
    
    Write-Host "`n" -NoNewline
    Write-Host "============================================================================" -ForegroundColor Green
    Write-Host "  $Environment CONFIGURATION" -ForegroundColor Green
    Write-Host "============================================================================" -ForegroundColor Green
    
    if (-not $Config) {
        Write-Host "   ❌ No configuration data available" -ForegroundColor Red
        return
    }
    
    # Environment info
    Write-Host "`n📌 ENVIRONMENT" -ForegroundColor Yellow
    Write-Host "   Name:         $($Config.environment.name)"
    Write-Host "   Debug:        $($Config.environment.debug)"
    Write-Host "   AIFlow Env:   $($Config.environment.aiflow_env)"
    
    # Chunking
    Write-Host "`n📄 CHUNKING SETTINGS" -ForegroundColor Yellow
    Write-Host "   Chunk Size:   $($Config.extraction_config.chunking.chunk_size_pages) pages"
    Write-Host "   Page Overlap: $($Config.extraction_config.chunking.page_overlap) pages"
    Write-Host "   Max Parallel: $($Config.extraction_config.chunking.max_chunks_parallel) chunks"
    
    # AI Engines
    Write-Host "`n🤖 AI ENGINE SETTINGS" -ForegroundColor Yellow
    Write-Host "   Engines:      $($Config.extraction_config.ai_engines.engines_list -join ', ')"
    Write-Host "   Gemini Model: $($Config.extraction_config.ai_engines.gemini_model)"
    Write-Host "   OpenAI Model: $($Config.extraction_config.ai_engines.openai_model)"
    Write-Host "   Max Tokens:   $($Config.extraction_config.ai_engines.openai_max_tokens)"
    Write-Host "   Temperature:  Gemini=$($Config.extraction_config.ai_engines.temperature.gemini), OpenAI=$($Config.extraction_config.ai_engines.temperature.openai)"
    
    # Ensemble
    Write-Host "`n🔄 ENSEMBLE EXTRACTION" -ForegroundColor Yellow
    $ensembleColor = if ($Config.advanced_validation.ensemble_extraction.enabled) { "Green" } else { "Red" }
    Write-Host "   Enabled:      $($Config.advanced_validation.ensemble_extraction.enabled)" -ForegroundColor $ensembleColor
    Write-Host "   Status:       $($Config.advanced_validation.ensemble_extraction.status)"
    if ($Config.advanced_validation.ensemble_extraction.enabled) {
        Write-Host "   Consensus:    $($Config.advanced_validation.ensemble_extraction.consensus_threshold * 100)%"
        Write-Host "   Strategy:     $($Config.advanced_validation.ensemble_extraction.voting_strategy)"
    }
    
    # Validation Layers
    Write-Host "`n✅ VALIDATION LAYERS" -ForegroundColor Yellow
    Write-Host "   Component Count:      $($Config.advanced_validation.validation_layers.component_count)"
    Write-Host "   Material Standards:   $($Config.advanced_validation.validation_layers.material_standards)"
    Write-Host "   Size Ranges:          $($Config.advanced_validation.validation_layers.size_ranges)"
    Write-Host "   Template Comparison:  $($Config.advanced_validation.validation_layers.template_comparison)"
    Write-Host "   Auto-Retry:           $($Config.advanced_validation.validation_layers.auto_retry)"
    
    # API Keys
    Write-Host "`n🔑 API KEYS STATUS" -ForegroundColor Yellow
    $openaiColor = if ($Config.api_keys_status.openai.configured) { "Green" } else { "Red" }
    $geminiColor = if ($Config.api_keys_status.gemini.configured) { "Green" } else { "Red" }
    Write-Host "   OpenAI:       $($Config.api_keys_status.openai.configured)" -ForegroundColor $openaiColor
    if ($Config.api_keys_status.openai.configured) {
        Write-Host "     Prefix:     $($Config.api_keys_status.openai.key_prefix)"
        Write-Host "     Length:     $($Config.api_keys_status.openai.key_length) chars"
    }
    Write-Host "   Gemini:       $($Config.api_keys_status.gemini.configured)" -ForegroundColor $geminiColor
    if ($Config.api_keys_status.gemini.configured) {
        Write-Host "     Prefix:     $($Config.api_keys_status.gemini.key_prefix)"
        Write-Host "     Length:     $($Config.api_keys_status.gemini.key_length) chars"
    }
    
    if ($Config.api_keys_status.warning) {
        Write-Host "`n   $($Config.api_keys_status.warning)" -ForegroundColor Red
    }
    
    # Expected Behavior
    Write-Host "`n📊 EXPECTED BEHAVIOR" -ForegroundColor Yellow
    Write-Host "   Mode:             $($Config.expected_behavior.mode)"
    Write-Host "   Accuracy:         $($Config.expected_behavior.expected_accuracy)"
    Write-Host "   Components/Class: $($Config.expected_behavior.expected_components_per_class)"
    
    if ($Config.expected_behavior.warnings.Count -gt 0) {
        Write-Host "`n   ⚠️  WARNINGS:" -ForegroundColor Red
        foreach ($warning in $Config.expected_behavior.warnings) {
            Write-Host "      $warning" -ForegroundColor Yellow
        }
    }
}

# Main execution
Write-Host ""

# Get tokens
$localToken = if ($localUsername -and $localPasswordPlain) { 
    Get-AuthToken -BaseUrl "http://localhost:8000/api/v1" -Username $localUsername -Password $localPasswordPlain 
} else { 
    $null 
}

$prodToken = if ($prodUsername -and $prodPasswordPlain) { 
    Get-AuthToken -BaseUrl "https://aiflowbackend-production.up.railway.app/api/v1" -Username $prodUsername -Password $prodPasswordPlain 
} else { 
    $null 
}

# Fetch configurations
$localConfig = Get-Configuration -Url $localUrl -Environment "LOCAL" -Token $localToken
$prodConfig = Get-Configuration -Url $productionUrl -Environment "PRODUCTION" -Token $prodToken

# Print configurations
if ($localConfig) {
    Print-Configuration -Config $localConfig -Environment "LOCAL DEVELOPMENT"
}

if ($prodConfig) {
    Print-Configuration -Config $prodConfig -Environment "PRODUCTION"
}

# Comparison Summary
Write-Host "`n" -NoNewline
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host "  COMPARISON SUMMARY" -ForegroundColor Cyan
Write-Host "============================================================================" -ForegroundColor Cyan

if ($localConfig -and $prodConfig) {
    Write-Host ""
    
    # Compare ensemble extraction
    if ($localConfig.advanced_validation.ensemble_extraction.enabled -ne $prodConfig.advanced_validation.ensemble_extraction.enabled) {
        Write-Host "⚠️  ENSEMBLE EXTRACTION MISMATCH" -ForegroundColor Red
        Write-Host "   Local:      $($localConfig.advanced_validation.ensemble_extraction.enabled)"
        Write-Host "   Production: $($prodConfig.advanced_validation.ensemble_extraction.enabled)"
        Write-Host "   Impact:     This is likely causing the 1858 vs 116 record difference!" -ForegroundColor Yellow
        Write-Host ""
    } else {
        Write-Host "✅ Ensemble extraction settings match" -ForegroundColor Green
    }
    
    # Compare chunk size
    if ($localConfig.extraction_config.chunking.chunk_size_pages -ne $prodConfig.extraction_config.chunking.chunk_size_pages) {
        Write-Host "⚠️  CHUNK SIZE MISMATCH" -ForegroundColor Red
        Write-Host "   Local:      $($localConfig.extraction_config.chunking.chunk_size_pages) pages"
        Write-Host "   Production: $($prodConfig.extraction_config.chunking.chunk_size_pages) pages"
        Write-Host ""
    } else {
        Write-Host "✅ Chunk size matches ($($localConfig.extraction_config.chunking.chunk_size_pages) pages)" -ForegroundColor Green
    }
    
    # Compare max tokens
    if ($localConfig.extraction_config.ai_engines.openai_max_tokens -ne $prodConfig.extraction_config.ai_engines.openai_max_tokens) {
        Write-Host "⚠️  MAX TOKENS MISMATCH" -ForegroundColor Red
        Write-Host "   Local:      $($localConfig.extraction_config.ai_engines.openai_max_tokens)"
        Write-Host "   Production: $($prodConfig.extraction_config.ai_engines.openai_max_tokens)"
        Write-Host ""
    } else {
        Write-Host "✅ Max tokens matches ($($localConfig.extraction_config.ai_engines.openai_max_tokens))" -ForegroundColor Green
    }
    
    # Compare API keys
    if ($localConfig.api_keys_status.openai.configured -ne $prodConfig.api_keys_status.openai.configured) {
        Write-Host "⚠️  OPENAI API KEY MISMATCH" -ForegroundColor Red
        Write-Host "   Local:      $($localConfig.api_keys_status.openai.configured)"
        Write-Host "   Production: $($prodConfig.api_keys_status.openai.configured)"
        Write-Host ""
    }
    
    if ($localConfig.api_keys_status.gemini.configured -ne $prodConfig.api_keys_status.gemini.configured) {
        Write-Host "⚠️  GEMINI API KEY MISMATCH" -ForegroundColor Red
        Write-Host "   Local:      $($localConfig.api_keys_status.gemini.configured)"
        Write-Host "   Production: $($prodConfig.api_keys_status.gemini.configured)"
        Write-Host ""
    }
    
    Write-Host "`n📋 RECOMMENDED ACTIONS:" -ForegroundColor Cyan
    Write-Host "   1. If ensemble extraction differs, update Railway environment variables"
    Write-Host "   2. If API keys missing in production, add to Railway dashboard"
    Write-Host "   3. If chunk size or max tokens differ, check for environment overrides"
    Write-Host "   4. Redeploy or restart production after environment changes"
    Write-Host ""
    
} else {
    Write-Host ""
    Write-Host "❌ Cannot compare - missing configuration data" -ForegroundColor Red
    Write-Host "   Please ensure both local and production endpoints are accessible" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host ""
