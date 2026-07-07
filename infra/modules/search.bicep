@description('Azure AI Search service name.')
param name string
param location string
param tags object
param privateByDefault bool
@description('Azure AI Search SKU (basic, standard, standard2, ...).')
param sku string = 'standard'
@description('Number of replicas (>=2 recommended for query HA / SLA).')
@minValue(1)
param replicaCount int = 2
@description('Number of partitions (scales index size and write throughput).')
@minValue(1)
param partitionCount int = 1
@description('Managed identity principal granted index data access.')
param principalId string
@description('Optional deployer principal granted index admin for setup.')
param deployerPrincipalId string = ''

// Search RBAC role IDs
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: { name: sku }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'default'
    semanticSearch: 'standard'
    authOptions: null
    disableLocalAuth: true
    publicNetworkAccess: privateByDefault ? 'disabled' : 'enabled'
  }
  identity: { type: 'SystemAssigned' }
}

resource indexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, principalId, searchIndexDataContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerIndexAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(search.id, deployerPrincipalId, searchServiceContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource deployerIndexData 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(search.id, deployerPrincipalId, searchIndexDataContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

output id string = search.id
output endpoint string = 'https://${search.name}.search.windows.net'
output principalId string = search.identity.principalId
