param name string
param location string
param skuName string = 'basic'
param applicationPrincipalId string = ''

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
  }
}

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

output endpoint string = 'https://${name}.search.windows.net'
output principalId string = searchService.identity.principalId
