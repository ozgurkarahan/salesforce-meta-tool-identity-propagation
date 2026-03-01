// ============================================================================
// Module: Salesforce OBO Connection (RemoteTool with AAD auth)
// Creates a RemoteTool connection on the Foundry project that passes the user's
// Azure AD token through to APIM. APIM handles the token exchange to Salesforce
// via JWT Bearer flow — the connection itself does no OAuth consent.
//
// Uses CognitiveServices/accounts/projects/connections@2025-04-01-preview.
// ============================================================================

@description('Name of the Cognitive Services account')
param cognitiveAccountName string

@description('Name of the AI Foundry project (child of cognitive account)')
param projectName string

@description('Salesforce MCP OBO endpoint URL via APIM')
param sfMcpOboEndpoint string

resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: cognitiveAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: cognitiveAccount
  name: projectName
}

resource sfOboConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'salesforce-obo'
  properties: {
    authType: 'AAD'
    category: 'RemoteTool'
    target: sfMcpOboEndpoint
    metadata: {
      type: 'custom_MCP'
    }
    isSharedToAll: true
  }
}

@description('Name of the SF OBO connection')
output connectionName string = sfOboConnection.name
