@description('Azure AI Foundry (AI Services) account name.')
param accountName string
@description('Foundry project name (child of the account).')
param projectName string
param location string
param tags object
param privateByDefault bool
param modelDeployments array
@description('Managed identity principal granted Foundry data-plane access.')
param principalId string
@description('Optional deployer principal granted Foundry user access for setup.')
param deployerPrincipalId string = ''

// Azure AI Developer + Cognitive Services User role IDs
var azureAIDeveloper = '64702f94-c441-49e6-a78b-ef80e0188fee'
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    publicNetworkAccess: privateByDefault ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: privateByDefault ? 'Deny' : 'Allow'
    }
    disableLocalAuth: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: projectName
    description: 'Multimodal Workplace Chatbot experiment project'
  }
}

@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = [
  for d in modelDeployments: {
    parent: account
    name: d.name
    sku: { name: 'GlobalStandard', capacity: d.capacity }
    properties: {
      model: { format: 'OpenAI', name: d.model, version: d.version }
      raiPolicyName: 'Microsoft.DefaultV2'
    }
  }
]

resource aiDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, azureAIDeveloper)
  scope: project
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIDeveloper)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource openAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, cognitiveServicesOpenAIUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerAIDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(project.id, deployerPrincipalId, azureAIDeveloper)
  scope: project
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIDeveloper)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource deployerCognitiveUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(account.id, deployerPrincipalId, cognitiveServicesUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

output accountId string = account.id
output projectId string = project.id
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${projectName}'
output accountEndpoint string = account.properties.endpoint
