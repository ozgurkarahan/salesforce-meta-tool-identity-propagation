// ============================================================================
// Module: Salesforce OAuth Connection (RemoteTool)
// Creates a RemoteTool connection on the Foundry project for Salesforce OAuth.
// The Foundry agent uses this connection to acquire SF OAuth tokens when calling
// Salesforce MCP tools via APIM. APIM validates SF JWT tokens with validate-jwt.
//
// Uses CognitiveServices/accounts/projects/connections@2025-04-01-preview.
// BCP037 warnings for group, connectorName, metadata.type, credentials,
// authorizationUrl, tokenUrl, refreshUrl, scopes are expected and safe to
// ignore — same pattern as mcp-oauth-connection.bicep.
//
// IMPORTANT: Connection MUST use category 'RemoteTool' + group 'GenericProtocol'.
// IMPORTANT: Bicep-created connections do NOT register the ApiHub connector —
// postprovision hook must DELETE and PUT via ARM REST to trigger ApiHub setup.
// ============================================================================

@description('Name of the Cognitive Services account')
param cognitiveAccountName string

@description('Name of the AI Foundry project (child of cognitive account)')
param projectName string

@description('Salesforce MCP endpoint URL via APIM')
param sfMcpEndpoint string

@description('Salesforce Connected App client ID (consumer key)')
param clientId string

@secure()
@description('Salesforce Connected App client secret (consumer secret)')
param clientSecret string

@description('Salesforce login URL for OAuth (My Domain URL enables Azure AD SSO)')
param sfLoginUrl string = 'https://login.salesforce.com'

resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: cognitiveAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: cognitiveAccount
  name: projectName
}

resource sfOAuthConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'salesforce-oauth'
  properties: {
    authType: 'OAuth2'
    category: 'RemoteTool'
    group: 'GenericProtocol'
    connectorName: 'salesforce-oauth'
    target: sfMcpEndpoint
    credentials: {
      clientId: clientId
      clientSecret: clientSecret
    }
    authorizationUrl: '${sfLoginUrl}/services/oauth2/authorize'
    tokenUrl: '${sfLoginUrl}/services/oauth2/token'
    refreshUrl: '${sfLoginUrl}/services/oauth2/token'
    scopes: ['api', 'refresh_token']
    metadata: {
      type: 'custom_MCP'
    }
    isSharedToAll: true
  }
}

@description('Name of the SF OAuth connection (used by postprovision hook)')
output connectionName string = sfOAuthConnection.name
