targetScope = 'subscription'

@description('Existing resource group that will contain the Search service.')
param resourceGroupName string = 'rg-admin-2684'

@description('Azure region for the Search service.')
param location string = 'eastus2'

@description('Globally unique Azure AI Search service name.')
param searchServiceName string = 'wtw-knowledge-search-2684'

@description('Search service SKU for the pilot.')
@allowed([
  'basic'
  'standard'
])
param searchSku string = 'basic'

@description('Optional Entra service principal object ID for the application that will index and query approved content.')
param applicationPrincipalId string = ''

resource searchResourceGroup 'Microsoft.Resources/resourceGroups@2023-07-01' existing = {
  name: resourceGroupName
}

module searchService './modules/search-service.bicep' = {
  name: 'search-service'
  scope: searchResourceGroup
  params: {
    location: location
    name: searchServiceName
    applicationPrincipalId: applicationPrincipalId
    skuName: searchSku
  }
}

output searchEndpoint string = searchService.outputs.endpoint
output searchPrincipalId string = searchService.outputs.principalId
