// =====================================================================
// Multimodal Workplace Chatbot on Microsoft Foundry — landing zone
// Implements docs/02-architecture.md (private-by-default, managed identity,
// Foundry project + models, AI Search, Storage, Key Vault, monitoring).
//
// NOTE: This is a reference template. For production, layer on the
// enterprise landing zone (hub-spoke, private DNS, Azure Policy, PIM)
// described in docs/02-architecture.md §13.2 / §15.
// =====================================================================
targetScope = 'resourceGroup'

@description('Base name used to derive resource names.')
param baseName string = 'mmchat'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Deploy data-plane services with public network access disabled.')
param privateByDefault bool = true

@description('Azure AI Search SKU.')
param searchSku string = 'standard'

@description('Region for Azure AI Search (defaults to the main location).')
param searchLocation string = location

@description('Azure AI Search replica count (>=2 recommended for query HA / SLA).')
@minValue(1)
param searchReplicaCount int = 2

@description('Object ID of the deployment principal granted data-plane roles for setup.')
param deployerPrincipalId string = ''

@description('Model deployments to create on the Foundry account.')
param modelDeployments array = [
  { name: 'gpt-4.1', model: 'gpt-4.1', version: '2025-04-14', capacity: 50 }
  { name: 'gpt-4.1-mini', model: 'gpt-4.1-mini', version: '2025-04-14', capacity: 50 }
  { name: 'gpt-4o', model: 'gpt-4o', version: '2024-11-20', capacity: 30 }
  { name: 'text-embedding-3-large', model: 'text-embedding-3-large', version: '1', capacity: 50 }
]

var suffix = uniqueString(resourceGroup().id, baseName)
var tags = {
  workload: 'multimodal-workplace-chatbot'
  app: baseName
  env: 'experiment'
  dataClass: 'confidential'
  owner: 'ai4ops-enablement'
}

module monitoring './modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: '${baseName}-${suffix}'
    location: location
    tags: tags
    privateByDefault: privateByDefault
  }
}

module identity './modules/identity.bicep' = {
  name: 'identity'
  params: {
    name: '${baseName}-${suffix}'
    location: location
    tags: tags
  }
}

module keyvault './modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    name: 'kv${take(suffix, 18)}'
    location: location
    tags: tags
    privateByDefault: privateByDefault
    principalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module storage './modules/storage.bicep' = {
  name: 'storage'
  params: {
    name: 'st${take(suffix, 20)}'
    location: location
    tags: tags
    privateByDefault: privateByDefault
    principalId: identity.outputs.principalId
  }
}

module search './modules/search.bicep' = {
  name: 'search'
  params: {
    name: '${baseName}-search-${suffix}'
    location: searchLocation
    tags: tags
    privateByDefault: privateByDefault
    sku: searchSku
    replicaCount: searchReplicaCount
    principalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module foundry './modules/ai-foundry.bicep' = {
  name: 'foundry'
  params: {
    accountName: '${baseName}-aifoundry-${suffix}'
    projectName: '${baseName}-project'
    location: location
    tags: tags
    privateByDefault: privateByDefault
    modelDeployments: modelDeployments
    principalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output SEARCH_ENDPOINT string = search.outputs.endpoint
output STORAGE_ACCOUNT_URL string = storage.outputs.blobEndpoint
output KEYVAULT_URI string = keyvault.outputs.vaultUri
output MANAGED_IDENTITY_CLIENT_ID string = identity.outputs.clientId
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
