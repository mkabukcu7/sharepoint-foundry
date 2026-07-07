@description('Key Vault name (3-24 chars, alphanumeric/dashes).')
param name string
param location string
param tags object
param privateByDefault bool
@description('Managed identity principal granted secret read.')
param principalId string
@description('Optional deployer principal granted secret admin for setup.')
param deployerPrincipalId string = ''

// Built-in role IDs
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var keyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: privateByDefault ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: privateByDefault ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource secretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, principalId, keyVaultSecretsUser)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource secretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(vault.id, deployerPrincipalId, keyVaultSecretsOfficer)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficer)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

output vaultUri string = vault.properties.vaultUri
output id string = vault.id
