@description('Base name for resources')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('Azure OpenAI endpoint URL (from cognitive module)')
param cognitiveEndpoint string

@description('Application Insights connection string for APIM diagnostics')
param appInsightsConnectionString string

@description('Log Analytics workspace resource ID for diagnostic settings')
param logAnalyticsWorkspaceId string

@description('Key Vault URI for certificate references (empty = no cert)')
param keyVaultUri string = ''

@description('Name of the SF JWT Bearer certificate in Key Vault (empty = no cert)')
param sfJwtBearerCertName string = ''

var openaiBackendUrl = '${cognitiveEndpoint}openai'

// --- APIM Instance ---
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' = {
  name: 'apim-${name}'
  location: location
  tags: tags
  sku: {
    name: 'StandardV2'
    capacity: 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: 'admin@sf-mcp-tool.dev'
    publisherName: 'Salesforce MCP Tool'
  }
}

// --- OpenAI Backend ---
resource openaiBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apim
  name: 'openai-backend'
  properties: {
    url: openaiBackendUrl
    protocol: 'http'
    title: 'Azure OpenAI'
  }
}

// --- Azure OpenAI API (AI Gateway) ---
resource openaiApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'azure-openai'
  properties: {
    displayName: 'Azure OpenAI'
    path: 'openai'
    protocols: ['https']
    serviceUrl: openaiBackendUrl
    subscriptionRequired: false
    apiType: 'http'
    format: 'rawxml'
    value: loadTextContent('../policies/ai-gateway-policy.xml')
  }
}

resource opChatCompletions 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: openaiApi
  name: 'chat-completions'
  properties: {
    displayName: 'Chat Completions'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/chat/completions'
    description: 'Creates a completion for the chat message'
    templateParameters: [
      {
        name: 'deployment-id'
        required: true
        type: 'string'
        description: 'Deployment ID (e.g. gpt-4o)'
      }
    ]
  }
}

resource opCompletions 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: openaiApi
  name: 'completions'
  properties: {
    displayName: 'Completions'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/completions'
    description: 'Creates a completion for the provided prompt'
    templateParameters: [
      {
        name: 'deployment-id'
        required: true
        type: 'string'
        description: 'Deployment ID (e.g. gpt-4o)'
      }
    ]
  }
}

resource opEmbeddings 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: openaiApi
  name: 'embeddings'
  properties: {
    displayName: 'Embeddings'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/embeddings'
    description: 'Creates an embedding vector for the input'
    templateParameters: [
      {
        name: 'deployment-id'
        required: true
        type: 'string'
        description: 'Deployment ID (e.g. text-embedding-ada-002)'
      }
    ]
  }
}

// --- APIM Logger (Application Insights) ---
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  parent: apim
  name: 'appinsights-logger'
  properties: {
    loggerType: 'applicationInsights'
    credentials: {
      connectionString: appInsightsConnectionString
    }
  }
}

// --- APIM Diagnostics (log request/response headers for all APIs) ---
// IMPORTANT: Response body bytes MUST be 0 at the All APIs scope.
// Non-zero values trigger response buffering that breaks MCP SSE streaming.
// See: https://learn.microsoft.com/en-us/azure/api-management/export-rest-mcp-server
resource apimDiagnostics 'Microsoft.ApiManagement/service/diagnostics@2024-06-01-preview' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    alwaysLog: 'allErrors'
    loggerId: apimLogger.id
    verbosity: 'verbose'
    logClientIp: true
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    frontend: {
      request: {
        headers: [
          'Authorization'
          'Content-Type'
          'Accept'
          'Host'
          'User-Agent'
          'X-Forwarded-For'
          'X-Forwarded-Host'
          'X-Request-ID'
          'X-MS-Client-Request-Id'
          'Mcp-Session-Id'
          'Cookie'
          'Sec-WebSocket-Protocol'
          'Connection'
          'Upgrade'
        ]
        body: {
          bytes: 8192
        }
      }
      response: {
        headers: [
          'WWW-Authenticate'
          'Content-Type'
          'Location'
          'X-MS-Request-Id'
          'Set-Cookie'
        ]
        body: {
          bytes: 0
        }
      }
    }
    backend: {
      request: {
        headers: [
          'Authorization'
          'Content-Type'
          'Host'
          'User-Agent'
        ]
        body: {
          bytes: 8192
        }
      }
      response: {
        headers: [
          'WWW-Authenticate'
          'Content-Type'
        ]
        body: {
          bytes: 0
        }
      }
    }
  }
}

// --- APIM Diagnostic Settings (GatewayLogs -> Log Analytics) ---
resource apimDiagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'apim-diagnostics'
  scope: apim
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'GatewayLogs'
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

// --- SF JWT Bearer Certificate (OBO mode) ---
// References the certificate from Key Vault so APIM policies can use it for JWT signing.
// Certificate is accessed via context.Deployment.Certificates["sf-jwt-bearer"] in policy XML.
resource sfJwtBearerCert 'Microsoft.ApiManagement/service/certificates@2024-06-01-preview' = if (!empty(keyVaultUri) && !empty(sfJwtBearerCertName)) {
  parent: apim
  name: 'sf-jwt-bearer'
  properties: {
    keyVault: {
      secretIdentifier: '${keyVaultUri}secrets/${sfJwtBearerCertName}'
    }
  }
}

output apimGatewayUrl string = apim.properties.gatewayUrl
output apimName string = apim.name
output apimPrincipalId string = apim.identity.principalId
output apimResourceId string = apim.id
