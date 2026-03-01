// ============================================================================
// Module: Salesforce MCP Container App
// Runs the Salesforce MCP server as a Container App. In passthrough mode,
// bearer tokens from APIM are forwarded directly to the Salesforce API.
// ============================================================================

@description('Base name for resources')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('ACR login server')
param registryLoginServer string

@description('ACR name')
param registryName string

@description('Container Apps Environment resource ID (shared with Orders API)')
param containerAppsEnvironmentId string

@description('Salesforce instance URL (e.g., https://myorg.my.salesforce.com)')
param sfInstanceUrl string = ''

@description('Application Insights connection string')
param appInsightsConnectionString string = ''

// Look up registry to get admin credentials
resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: registryName
}

// --- Salesforce MCP Container App ---
resource sfMcpApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-sf-mcp'
  location: location
  tags: union(tags, {
    'azd-service-name': 'salesforce-mcp'
  })
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryLoginServer
          username: registry.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: registry.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'salesforce-mcp'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'SF_BEARER_PASSTHROUGH'
              value: 'true'
            }
            {
              name: 'SF_INSTANCE_URL'
              value: sfInstanceUrl
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'salesforce-mcp'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output sfMcpFqdn string = sfMcpApp.properties.configuration.ingress.fqdn
output sfMcpAppName string = sfMcpApp.name
