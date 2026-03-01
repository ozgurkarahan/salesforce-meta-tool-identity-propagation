// ============================================================================
// Module: APIM Salesforce MCP OBO Reverse Proxy
// Same backend as the existing SF MCP API, but with Azure AD token validation
// and JWT Bearer token exchange (On-Behalf-Of) instead of SF JWT passthrough.
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

// --------------------------------------------------------------------------
// Reference existing APIM instance
// --------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

// --------------------------------------------------------------------------
// Named Values for OBO policies
// APIMGatewayURL is created by the apim-sf-mcp module (always deployed).
// This module must depend on apim-sf-mcp in main.bicep to avoid race conditions.
// --------------------------------------------------------------------------
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

// --------------------------------------------------------------------------
// Salesforce MCP OBO API (HTTP reverse proxy with OBO token exchange)
// --------------------------------------------------------------------------
resource sfMcpOboApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'salesforce-mcp-obo'
  properties: {
    displayName: 'Salesforce MCP Server (OBO)'
    description: 'Reverse proxy for Salesforce MCP server with Azure AD → SF JWT Bearer OBO exchange.'
    path: 'salesforce-mcp-obo'
    protocols: [
      'https'
    ]
    serviceUrl: 'https://${sfMcpFqdn}'
    subscriptionRequired: false
    apiType: 'http'
  }
}

// Wildcard operations — route all HTTP methods to the SF MCP backend
resource sfMcpOboPostOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfMcpOboApi
  name: 'sf-mcp-obo-post'
  properties: {
    displayName: 'POST (all paths)'
    method: 'POST'
    urlTemplate: '/*'
  }
}

resource sfMcpOboGetOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfMcpOboApi
  name: 'sf-mcp-obo-get'
  properties: {
    displayName: 'GET (all paths)'
    method: 'GET'
    urlTemplate: '/*'
  }
}

resource sfMcpOboDeleteOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfMcpOboApi
  name: 'sf-mcp-obo-delete'
  properties: {
    displayName: 'DELETE (all paths)'
    method: 'DELETE'
    urlTemplate: '/*'
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
  // APIMGatewayURL Named Value is created by apim-sf-mcp module (cross-module).
  // Ordering is enforced via dependsOn in main.bicep (apimSfMcpObo depends on apimSfMcp).
  dependsOn: [ tenantIdNV ]
}

// --------------------------------------------------------------------------
// Outputs
// --------------------------------------------------------------------------
@description('Salesforce MCP OBO endpoint URL via APIM')
output sfMcpOboEndpoint string = '${apim.properties.gatewayUrl}/salesforce-mcp-obo/mcp'
