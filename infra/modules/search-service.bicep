param name string
param location string
param skuName string = 'basic'
param applicationPrincipalId string = ''

@allowed([
  'free'
  'standard'
])
param semanticSearchTier string = 'free'

resource searchService 'Microsoft.Search/searchServices@2025-02-01-preview' = {
  name: name
  location: location
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    disableLocalAuth: true
    authOptions: null
    hostingMode: 'default'
    partitionCount: 1
    publicNetworkAccess: 'enabled'
    replicaCount: 1
    // The librarian decides whether it has evidence from the semantic reranker
    // score. Hybrid RRF scores cannot separate supported from unsupported
    // questions, so without this a fresh deployment cannot abstain correctly.
    semanticSearch: semanticSearchTier
  }
}

// Document read/write for indexing and querying approved content.
resource searchIndexDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(applicationPrincipalId)) {
  name: guid(searchService.id, applicationPrincipalId, 'Search Index Data Contributor')
  scope: searchService
  properties: {
    principalId: applicationPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
    )
  }
}

// The application calls SearchIndexClient.create_or_update_index at startup,
// which is index schema management rather than a document operation. Data
// Contributor alone returns 403 there, so the service-level role is required.
resource searchServiceContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(applicationPrincipalId)) {
  name: guid(searchService.id, applicationPrincipalId, 'Search Service Contributor')
  scope: searchService
  properties: {
    principalId: applicationPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
    )
  }
}

output endpoint string = 'https://${name}.search.windows.net'
output principalId string = searchService.identity.principalId
