@description('Base name for resources')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('Log Analytics workspace resource ID for diagnostic settings')
param logAnalyticsWorkspaceId string

@description('Suffix for the AI Services account name (increment after azd down --purge)')
param accountSuffix string = '3'

@description('App Insights resource ID for Foundry monitoring (empty = skip)')
param appInsightsId string = ''

@description('App Insights connection string for Foundry monitoring')
param appInsightsConnectionString string = ''

// --- AI Services Account (Foundry parent — replaces ML Hub pattern) ---
resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'aoai-${name}${accountSuffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: 'aoai-${name}${accountSuffix}'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

// --- AI Foundry Project (child of account — no Hub needed) ---
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: cognitiveAccount
  name: 'aiproj-${name}'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Salesforce MCP Tool Project'
    displayName: 'Salesforce MCP Tool Project'
  }
}

// --- AzureOpenAI Connection (account-level, AAD auth, shared to all projects) ---
resource aoaiConnection 'Microsoft.CognitiveServices/accounts/connections@2025-04-01-preview' = {
  parent: cognitiveAccount
  name: 'aoai-connection'
  properties: {
    category: 'AzureOpenAI'
    authType: 'AAD'
    isSharedToAll: true
    target: cognitiveAccount.properties.endpoints['OpenAI Language Model Instance API']
    metadata: {
      ApiType: 'azure'
      ResourceId: cognitiveAccount.id
    }
  }
}

// --- App Insights Connection (account-level, shared to all projects for Foundry monitoring) ---
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/connections@2025-04-01-preview' = if (!empty(appInsightsId)) {
  parent: cognitiveAccount
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    authType: 'ApiKey'
    target: appInsightsId
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
    }
    credentials: {
      key: appInsightsConnectionString
    }
  }
}

// --- gpt-4o Model Deployment ---
resource gpt4o 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: cognitiveAccount
  name: 'gpt-4o'
  sku: {
    name: 'Standard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
  }
}

// --- Diagnostic Settings (Audit + RequestResponse → Log Analytics) ---
resource cognitiveDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'cognitive-diagnostics'
  scope: cognitiveAccount
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'Audit'
        enabled: true
      }
      {
        category: 'RequestResponse'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output cognitiveAccountId string = cognitiveAccount.id
output cognitiveAccountName string = cognitiveAccount.name
output cognitiveEndpoint string = cognitiveAccount.properties.endpoint
output openaiEndpoint string = cognitiveAccount.properties.endpoints['OpenAI Language Model Instance API']
output projectId string = project.id
output projectName string = project.name
output projectEndpoint string = 'https://${cognitiveAccount.name}.services.ai.azure.com/api/projects/${project.name}'
