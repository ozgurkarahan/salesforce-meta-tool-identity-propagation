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

@description('Container Apps Environment resource ID')
param containerAppsEnvironmentId string

@description('AI Foundry project endpoint')
param projectEndpoint string

@description('Application Insights connection string')
param appInsightsConnectionString string = ''

@description('Chat App Entra client ID (set by postprovision)')
param chatAppEntraClientId string = ''

@description('Azure AD tenant ID')
param tenantId string = ''

@description('Log Analytics workspace GUID for debug log tail')
param logAnalyticsWorkspaceGuid string = ''

@description('Bot Service managed identity app ID')
param agentBotMsaAppId string = ''

@description('JSON config for multi-agent support (overrides AGENT_NAME when set)')
param agentsConfig string = ''

@description('JSON list of Foundry projects for dynamic agent discovery [{name, endpoint}]')
param foundryProjects string = ''

@description('Container image to run (from SERVICE_CHAT_APP_IMAGE_NAME); empty = helloworld placeholder until azd deploy')
param imageName string = ''

// Look up registry to get admin credentials
resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: registryName
}

// --- Chat App Container App ---
resource chatApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-chat-app'
  location: location
  tags: union(tags, {
    'azd-service-name': 'chat-app'
  })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['GET', 'POST', 'OPTIONS']
          allowedHeaders: ['*']
        }
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
          name: 'chat-app'
          image: !empty(imageName) ? imageName : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'AI_FOUNDRY_PROJECT_ENDPOINT'
              value: projectEndpoint
            }
            {
              name: 'AGENT_NAME'
              value: 'salesforce-assistant'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'chat-app'
            }
            ...(!empty(chatAppEntraClientId) ? [
              {
                name: 'CHAT_APP_ENTRA_CLIENT_ID'
                value: chatAppEntraClientId
              }
            ] : [])
            ...(!empty(tenantId) ? [
              {
                name: 'TENANT_ID'
                value: tenantId
              }
            ] : [])
            ...(!empty(logAnalyticsWorkspaceGuid) ? [
              {
                name: 'LOG_ANALYTICS_WORKSPACE_ID'
                value: logAnalyticsWorkspaceGuid
              }
            ] : [])
            ...(!empty(agentBotMsaAppId) ? [
              {
                name: 'AGENT_BOT_MSA_APP_ID'
                value: agentBotMsaAppId
              }
            ] : [])
            ...(!empty(agentsConfig) ? [
              {
                name: 'AGENTS_CONFIG'
                value: agentsConfig
              }
            ] : [])
            ...(!empty(foundryProjects) ? [
              {
                name: 'FOUNDRY_PROJECTS'
                value: foundryProjects
              }
            ] : [])
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

output chatAppFqdn string = chatApp.properties.configuration.ingress.fqdn
output chatAppName string = chatApp.name
output chatAppPrincipalId string = chatApp.identity.principalId
