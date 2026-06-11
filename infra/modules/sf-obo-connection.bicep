// ============================================================================
// Module: Salesforce OBO Connection (RemoteTool with OAuth2 "bring-your-own" auth)
// Creates a RemoteTool connection on the Foundry project that runs an OAuth2
// authorization-code + PKCE flow against a dedicated Entra app and injects the
// resulting user token as a Bearer to APIM. APIM then performs the Salesforce
// JWT-Bearer exchange.
//
// IMPORTANT: Foundry rejects authType 'UserEntraToken' against *.azure-api.net
// (Microsoft-owned) hosts with "Cannot pass Microsoft token to untrusted MCP
// endpoint". OAuth2 BYO is the supported pattern for a custom MCP server behind
// APIM. This declarative resource is a baseline; hooks/postprovision.py
// (update_obo_connection) re-PUTs it via REST (api-version 2025-09-01) to mint
// the consent redirectUrl and register it on the BYO app.
//
// Uses CognitiveServices/accounts/projects/connections@2025-04-01-preview.
// Note: authType 'AAD' is NOT valid for RemoteTool connections. Valid types:
//   None, CustomKeys, ProjectManagedIdentity, OAuth2, UserEntraToken,
//   AgentUserImpersonation, AgenticIdentityToken, AgenticUser,
//   UserTokenAndProjectManagedIdentity
// ============================================================================

@description('Name of the Cognitive Services account')
param cognitiveAccountName string

@description('Name of the AI Foundry project (child of cognitive account)')
param projectName string

@description('Salesforce MCP OBO endpoint URL via APIM')
param sfMcpOboEndpoint string

@description('Entra app (client) ID used for the OAuth2 BYO flow (Option C app "salesforce-mcp-cli")')
param oauthClientId string = '0bb553db-1b45-4f21-97a6-2586c987a4d5'

@description('Entra tenant ID for the OAuth2 authority endpoints')
param tenantId string = tenant().tenantId

var authority = '${environment().authentication.loginEndpoint}${tenantId}/oauth2/v2.0'

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
    authType: 'OAuth2'
    category: 'RemoteTool'
    target: sfMcpOboEndpoint
    metadata: {
      type: 'custom_MCP'
    }
    credentials: {
      clientId: oauthClientId
      clientSecret: ''
    }
    // tokenUrl/authorizationUrl/refreshUrl/scopes are top-level OAuth2 MCP
    // fields added in api-version 2025-09-01; the 2025-04-01-preview Bicep type
    // does not yet declare them (BCP037), but the resource provider accepts and
    // requires them. hooks/postprovision.py re-PUTs this connection via REST
    // (2025-09-01) to mint the consent redirectUrl and register it on the app.
    #disable-next-line BCP037
    tokenUrl: '${authority}/token'
    #disable-next-line BCP037
    authorizationUrl: '${authority}/authorize'
    #disable-next-line BCP037
    refreshUrl: '${authority}/token'
    #disable-next-line BCP037
    scopes: [
      'api://${oauthClientId}/access_as_user'
      'offline_access'
    ]
    isSharedToAll: true
  }
}

@description('Name of the SF OBO connection')
output connectionName string = sfOboConnection.name
