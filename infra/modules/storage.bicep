@description('Storage account name (3-24 chars, lowercase alphanumeric).')
param name string
param location string
param tags object
param privateByDefault bool
@description('Managed identity principal granted blob data access.')
param principalId string

var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: privateByDefault ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: privateByDefault ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 30 }
  }
}

resource ingestContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'ingest'
  properties: { publicAccess: 'None' }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, storageBlobDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output id string = storage.id
output blobEndpoint string = storage.properties.primaryEndpoints.blob
