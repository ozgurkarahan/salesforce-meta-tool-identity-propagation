// Azure Bot for a V3 managed-harness agent published to Microsoft 365 / Teams through
// its agent endpoint (Foundry "Publish to Teams and Microsoft Copilot", REST steps 1, 2, 4).
// The bot's msaAppId is the agent's instance identity, and its endpoint is the agent's
// Activity Protocol route, not a legacy Agent Application.

@description('Bot Service resource name')
param botName string

@description('Bot display name')
param displayName string

@description('Agent instance identity client ID (GET /agents/<name> -> instance_identity.client_id)')
param msaAppId string

@description('Microsoft Entra tenant ID')
param tenantId string

@description('Cognitive Services account name (for endpoint URL)')
param cognitiveAccountName string

@description('Foundry project name (for endpoint URL)')
param projectName string

@description('Foundry agent name')
param agentName string

@description('Bot Service SKU')
param sku string = 'F0'

param tags object = {}

resource botService 'Microsoft.BotService/botServices@2023-09-15-preview' = {
  name: botName
  location: 'global'
  kind: 'azurebot'
  sku: { name: sku }
  tags: tags
  properties: {
    displayName: displayName
    endpoint: 'https://${cognitiveAccountName}.services.ai.azure.com/api/projects/${projectName}/agents/${agentName}/endpoint/protocols/activityProtocol?api-version=v1'
    msaAppId: msaAppId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
    publicNetworkAccess: 'Disabled'
  }
}

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

output botServiceName string = botService.name
output botServiceId string = botService.id
output botServiceEndpoint string = botService.properties.endpoint
