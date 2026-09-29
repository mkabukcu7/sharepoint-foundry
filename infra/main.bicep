targetScope = 'subscription'

@description('Existing resource group that will contain the Search service.')
param resourceGroupName string

@description('Azure region for the Search service.')
param location string

@description('Globally unique Azure AI Search service name.')
param searchServiceName string

@description('Search service SKU for the pilot.')
@allowed([
  'basic'
  'standard'
])
param searchSku string = 'basic'

@description('Entra service principal object ID for the application that will index and query approved content.')
param applicationPrincipalId string

@description('Semantic ranker tier. The librarian judges whether it has evidence from the reranker score, so this cannot be disabled without losing abstention.')
@allowed([
  'free'
  'standard'
])
param semanticSearchTier string = 'free'

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
    semanticSearchTier: semanticSearchTier
  }
}

output searchEndpoint string = searchService.outputs.endpoint
output searchPrincipalId string = searchService.outputs.principalId
