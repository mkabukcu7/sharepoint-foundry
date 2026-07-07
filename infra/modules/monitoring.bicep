@description('Base name for monitoring resources.')
param name string
param location string
param tags object
@description('When true, restrict App Insights ingestion/query to private networks.')
param privateByDefault bool = false

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${name}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
    features: { searchVersion: 1 }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${name}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    publicNetworkAccessForIngestion: privateByDefault ? 'Disabled' : 'Enabled'
    publicNetworkAccessForQuery: privateByDefault ? 'Disabled' : 'Enabled'
  }
}

output logAnalyticsId string = logAnalytics.id
output appInsightsId string = appInsights.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
