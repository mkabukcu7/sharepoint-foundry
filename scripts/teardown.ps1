<#
.SYNOPSIS
    Delete the Multimodal Workplace Chatbot resource group and purge the
    soft-deleted AI Services (Foundry) account and Key Vault so the base name
    can be reused.

.EXAMPLE
    ./scripts/teardown.ps1 -ResourceGroup rg-mmchat
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = "rg-mmchat",
    [switch]$Purge
)

$ErrorActionPreference = "Stop"

Write-Host "==> Collecting resources to purge before deletion..." -ForegroundColor Cyan
$aiAccounts = az cognitiveservices account list -g $ResourceGroup `
    --query "[].{name:name,location:location}" -o json | ConvertFrom-Json
$vaults = az keyvault list -g $ResourceGroup --query "[].{name:name,location:location}" -o json | ConvertFrom-Json

Write-Host "==> Deleting resource group '$ResourceGroup'..." -ForegroundColor Yellow
az group delete -n $ResourceGroup --yes --no-wait

if ($Purge) {
    Write-Host "==> Purging soft-deleted AI Services accounts..." -ForegroundColor Cyan
    foreach ($a in $aiAccounts) {
        az cognitiveservices account purge -n $a.name -l $a.location -g $ResourceGroup 2>$null
    }
    Write-Host "==> Purging soft-deleted Key Vaults..." -ForegroundColor Cyan
    foreach ($v in $vaults) {
        az keyvault purge -n $v.name -l $v.location 2>$null
    }
}

Write-Host "`n==> Teardown initiated. Group deletion runs in the background." -ForegroundColor Green
Write-Host "    Re-run with -Purge to reclaim soft-deleted Foundry/Key Vault names." -ForegroundColor Gray
