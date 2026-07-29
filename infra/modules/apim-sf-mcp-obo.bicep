// ============================================================================
// Module: APIM Salesforce MCP OBO (native MCP type)
// Uses APIM's native 'mcp' API type with a backend resource pointing to the
// SF MCP Container App. Azure AD token validation and JWT Bearer OBO exchange.
//
// APIM validates the Azure AD token, creates a JWT Bearer assertion signed
// with a Key Vault certificate, exchanges it at the SF token endpoint, and
// forwards the resulting SF access token to the MCP server backend.
//
// Includes RFC 9728 Protected Resource Metadata (PRM) endpoint advertising
// Azure AD as the authorization server.
// ============================================================================

@description('Name of the existing API Management instance')
param apimName string

@description('Salesforce MCP Container App FQDN')
param sfMcpFqdn string

@description('Azure AD tenant ID')
param tenantId string

@description('Salesforce Connected App client ID for OBO (consumer key)')
param sfOboClientId string = 'placeholder-updated-by-hook'

@description('Salesforce login URL for JWT Bearer token exchange')
param sfOboLoginUrl string = 'https://login.salesforce.com'

@description('Thumbprint of the SF JWT Bearer signing certificate in APIM')
param sfJwtBearerCertThumbprint string = ''

@description('SF username for the service account used for user lookups')
param sfServiceAccountUsername string = ''

@description('Name of the JWT claim containing the user identity (e.g., oid for Azure AD, sub for Okta/PingFed)')
param identityClaimName string = 'oid'

@description('Client ID of the Entra app used by Foundry OAuth2 identity-passthrough MCP connections (validate-jwt accepts audience api://<this>)')
param mcpOauthClientId string = ''

// --------------------------------------------------------------------------
// Reference existing APIM instance
// --------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

// --------------------------------------------------------------------------
// Named Values for OBO policies
// --------------------------------------------------------------------------
resource apimGatewayUrlNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'APIMGatewayURL'
  properties: {
    displayName: 'APIMGatewayURL'
    value: apim.properties.gatewayUrl
    secret: false
  }
}

resource tenantIdNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'TenantId'
  properties: {
    displayName: 'TenantId'
    value: tenantId
    secret: false
  }
}

resource sfOboClientIdNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'SfOboClientId'
  properties: {
    displayName: 'SfOboClientId'
    value: sfOboClientId
    secret: false
  }
}

resource sfOboLoginUrlNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'SfOboLoginUrl'
  properties: {
    displayName: 'SfOboLoginUrl'
    value: sfOboLoginUrl
    secret: false
  }
}

resource sfJwtBearerCertThumbprintNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = if (!empty(sfJwtBearerCertThumbprint)) {
  parent: apim
  name: 'SfJwtBearerCertThumbprint'
  properties: {
    displayName: 'SfJwtBearerCertThumbprint'
    value: sfJwtBearerCertThumbprint
    secret: false
  }
}

resource sfServiceAccountUsernameNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = if (!empty(sfServiceAccountUsername)) {
  parent: apim
  name: 'SfServiceAccountUsername'
  properties: {
    displayName: 'SfServiceAccountUsername'
    value: sfServiceAccountUsername
    secret: false
  }
}

resource identityClaimNameNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'IdentityClaimName'
  properties: {
    displayName: 'IdentityClaimName'
    value: identityClaimName
    secret: false
  }
}

// Always created (the policy references it); 'not-configured' yields the inert
// audience api://not-configured until MCP_OAUTH_CLIENT_ID is set.
resource mcpOauthClientIdNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'McpOauthClientId'
  properties: {
    displayName: 'McpOauthClientId'
    value: !empty(mcpOauthClientId) ? mcpOauthClientId : 'not-configured'
    secret: false
  }
}

// --------------------------------------------------------------------------
// Backend — points APIM to the SF MCP Container App
// --------------------------------------------------------------------------
resource sfMcpBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apim
  name: 'sf-mcp-backend'
  properties: {
    url: 'https://${sfMcpFqdn}'
    protocol: 'http'
    title: 'Salesforce MCP Server'
  }
}

// --------------------------------------------------------------------------
// Salesforce MCP OBO API (native MCP type with OBO token exchange)
// --------------------------------------------------------------------------
resource sfMcpOboApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'salesforce-mcp-obo'
  properties: {
    displayName: 'Salesforce MCP Server (OBO)'
    description: 'Native MCP API with Azure AD → SF JWT Bearer OBO exchange.'
    path: 'salesforce-mcp-obo'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    type: 'mcp'
    backendId: sfMcpBackend.name
    mcpProperties: {
      endpoints: {
        mcp: {
          uriTemplate: '/mcp'
        }
      }
    }
  }
}

// --------------------------------------------------------------------------
// API-level policy (Azure AD validate + JWT Bearer exchange + cache)
// --------------------------------------------------------------------------
resource sfMcpOboApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: sfMcpOboApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/sf-mcp-obo-policy.xml')
  }
  dependsOn: [
    tenantIdNV
    sfOboClientIdNV
    sfOboLoginUrlNV
    sfJwtBearerCertThumbprintNV
    sfServiceAccountUsernameNV
    identityClaimNameNV
    mcpOauthClientIdNV
  ]
}

// --------------------------------------------------------------------------
// PRM endpoint (RFC 9728 Protected Resource Metadata — anonymous access)
// Advertises Azure AD as authorization server (not Salesforce)
// --------------------------------------------------------------------------
resource sfOboPrmApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'salesforce-mcp-obo-prm'
  properties: {
    displayName: 'SF MCP OBO Protected Resource Metadata'
    path: 'salesforce-mcp-obo/.well-known'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    apiType: 'http'
  }
}

resource sfOboPrmOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfOboPrmApi
  name: 'sf-obo-oauth-protected-resource'
  properties: {
    displayName: 'SF OBO Protected Resource Metadata'
    method: 'GET'
    urlTemplate: '/oauth-protected-resource'
  }
}

resource sfOboPrmOpPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-06-01-preview' = {
  parent: sfOboPrmOp
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/sf-mcp-obo-prm-policy.xml')
  }
  dependsOn: [ tenantIdNV, apimGatewayUrlNV ]
}

// --------------------------------------------------------------------------
// Outputs
// --------------------------------------------------------------------------
@description('Salesforce MCP OBO endpoint URL via APIM')
output sfMcpOboEndpoint string = '${apim.properties.gatewayUrl}/salesforce-mcp-obo/mcp'
