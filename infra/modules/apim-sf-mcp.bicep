// ============================================================================
// Module: APIM Salesforce MCP Reverse Proxy
// Pure HTTP reverse proxy in front of the Salesforce MCP Container App.
// Uses validate-jwt with Salesforce OIDC discovery (not validate-azure-ad-token).
// Includes RFC 9728 Protected Resource Metadata (PRM) endpoint.
//
// NOT using apiType: 'mcp' — the SF MCP server is standalone, APIM is a proxy.
// ============================================================================

@description('Name of the existing API Management instance')
param apimName string

@description('Salesforce MCP Container App FQDN')
param sfMcpFqdn string

@description('Salesforce org instance URL (e.g., https://myorg.my.salesforce.com)')
param sfInstanceUrl string = 'https://login.salesforce.com'

// --------------------------------------------------------------------------
// Reference existing APIM instance
// --------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

// --------------------------------------------------------------------------
// Named Values for SF MCP policies
// SF JWT tokens use org-specific issuer/audience (the instance URL, not
// login.salesforce.com). The kid is also org-specific, so OIDC discovery
// must point at the instance URL for correct key resolution.
// --------------------------------------------------------------------------
resource sfInstanceUrlNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'SfInstanceUrl'
  properties: {
    displayName: 'SfInstanceUrl'
    value: sfInstanceUrl
    secret: false
  }
}

// APIMGatewayURL Named Value — created here (no Orders MCP module to create it).
resource apimGatewayUrlNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'APIMGatewayURL'
  properties: {
    displayName: 'APIMGatewayURL'
    value: apim.properties.gatewayUrl
    secret: false
  }
}

// --------------------------------------------------------------------------
// Salesforce MCP API (HTTP reverse proxy with wildcard operations)
// --------------------------------------------------------------------------
resource sfMcpApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'salesforce-mcp'
  properties: {
    displayName: 'Salesforce MCP Server'
    description: 'Reverse proxy for Salesforce MCP server with JWT validation.'
    path: 'salesforce-mcp'
    protocols: [
      'https'
    ]
    serviceUrl: 'https://${sfMcpFqdn}'
    subscriptionRequired: false
    apiType: 'http'
  }
}

// Wildcard operations — route all HTTP methods to the SF MCP backend
resource sfMcpPostOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfMcpApi
  name: 'sf-mcp-post'
  properties: {
    displayName: 'POST (all paths)'
    method: 'POST'
    urlTemplate: '/*'
  }
}

resource sfMcpGetOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfMcpApi
  name: 'sf-mcp-get'
  properties: {
    displayName: 'GET (all paths)'
    method: 'GET'
    urlTemplate: '/*'
  }
}

resource sfMcpDeleteOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfMcpApi
  name: 'sf-mcp-delete'
  properties: {
    displayName: 'DELETE (all paths)'
    method: 'DELETE'
    urlTemplate: '/*'
  }
}

// --------------------------------------------------------------------------
// API-level policy (validate-jwt with SF OIDC discovery + WWW-Authenticate 401)
// --------------------------------------------------------------------------
resource sfMcpApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: sfMcpApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/sf-mcp-api-policy.xml')
  }
  dependsOn: [ sfInstanceUrlNV, apimGatewayUrlNV ]
}

// --------------------------------------------------------------------------
// PRM endpoint (RFC 9728 Protected Resource Metadata — anonymous access)
// --------------------------------------------------------------------------
resource sfPrmApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'salesforce-mcp-prm'
  properties: {
    displayName: 'SF MCP Protected Resource Metadata'
    path: 'salesforce-mcp/.well-known'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    apiType: 'http'
  }
}

resource sfPrmOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: sfPrmApi
  name: 'sf-oauth-protected-resource'
  properties: {
    displayName: 'SF Protected Resource Metadata'
    method: 'GET'
    urlTemplate: '/oauth-protected-resource'
  }
}

resource sfPrmOpPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-06-01-preview' = {
  parent: sfPrmOp
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/sf-mcp-prm-policy.xml')
  }
  dependsOn: [ sfInstanceUrlNV, apimGatewayUrlNV ]
}

// --------------------------------------------------------------------------
// Outputs
// --------------------------------------------------------------------------
@description('Salesforce MCP endpoint URL via APIM')
output sfMcpEndpoint string = '${apim.properties.gatewayUrl}/salesforce-mcp/mcp'
