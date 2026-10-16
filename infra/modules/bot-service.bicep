@description('Base name for resources (used as fallback for bot name)')
param name string

@description('Bot Service resource name (override to adopt an existing bot)')
param botName string = 'agent-bot-${name}'

@description('Cognitive Services account name (for endpoint URL)')
param cognitiveAccountName string

@description('Foundry project name (for endpoint URL)')
param projectName string

@description('Agent Application name')
param applicationName string = 'salesforce-assistant'

@description('Bot display name')
param displayName string = 'Salesforce Assistant'

@description('Foundry-managed identity client ID (from Agent Application)')
param msaAppId string

@description('Azure AD tenant ID')
param tenantId string

@description('App Insights instrumentation key (optional)')
param appInsightsKey string = ''

// --- Bot Service ---
resource botService 'Microsoft.BotService/botServices@2023-09-15-preview' = {
  name: botName
  location: 'global'
  kind: 'azurebot'
  sku: { name: 'S1' }
  properties: {
    displayName: displayName
    description: 'Bot service for AI agent'
    endpoint: 'https://${cognitiveAccountName}.services.ai.azure.com/api/projects/${projectName}/applications/${applicationName}/protocols/activityprotocol?api-version=2025-11-15-preview'
    msaAppId: msaAppId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
    developerAppInsightKey: !empty(appInsightsKey) ? appInsightsKey : null
  }
}

// --- Teams Channel ---
resource teamsChannel 'Microsoft.BotService/botServices/channels@2023-09-15-preview' = {
  parent: botService
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
      deploymentEnvironment: 'CommercialDeployment'
    }
  }
}

// --- DirectLine Channel ---
resource directLineChannel 'Microsoft.BotService/botServices/channels@2023-09-15-preview' = {
  parent: botService
  name: 'DirectLineChannel'
  location: 'global'
  properties: {
    channelName: 'DirectLineChannel'
    properties: {
      sites: [
        {
          siteName: 'Default Site'
          isEnabled: true
          isV1Enabled: true
          isV3Enabled: true
        }
      ]
    }
  }
}

output botServiceName string = botService.name
output botServiceEndpoint string = botService.properties.endpoint
