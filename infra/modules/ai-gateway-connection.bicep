@description('Name of the Cognitive Services account')
param cognitiveAccountName string

@description('Name of the AI Foundry project (child of cognitive account)')
param projectName string

@description('APIM gateway URL (e.g. https://apim-identity-poc.azure-api.net)')
param apimGatewayUrl string

@description('APIM resource ID (full ARM resource ID)')
param apimResourceId string

resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: cognitiveAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: cognitiveAccount
  name: projectName
}

resource apimConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'apim-gateway'
  properties: {
    category: 'ApiManagement'
    authType: 'AAD'
    isSharedToAll: true
    target: '${apimGatewayUrl}/openai'
    metadata: {
      ResourceId: apimResourceId
      ApiType: 'azure'
    }
  }
}
