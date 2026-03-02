targetScope = 'subscription'

@description('Name of the azd environment')
@minLength(1)
@maxLength(64)
param environmentName string

@description('Azure region for all resources')
param location string = 'swedencentral'

@description('Suffix for the CognitiveServices account name (increment after azd down --purge)')
param cognitiveAccountSuffix string = ''

@description('Salesforce instance URL (from azd env set SF_INSTANCE_URL)')
param sfInstanceUrl string = ''

@description('Salesforce Connected App client ID (consumer key)')
param sfConnectedAppClientId string = 'placeholder-updated-by-hook'

@description('Name of the SF JWT Bearer certificate in Key Vault (uploaded separately)')
param sfJwtBearerCertName string = 'sf-jwt-bearer'

@description('Thumbprint of the SF JWT Bearer signing certificate (for APIM policy cert lookup)')
param sfJwtBearerCertThumbprint string = ''

@description('SF service account username for user lookups in OBO flow')
param sfServiceAccountUsername string = ''

@description('Name of the JWT claim containing the user identity (oid for Azure AD, sub for Okta/PingFed)')
param identityClaimName string = 'oid'

var baseName = toLower(environmentName)
var resourceToken = toLower(uniqueString(subscription().id, baseName, location))
var tags = {
  'azd-env-name': environmentName
  project: baseName
}

// --- Resource Group ---
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${baseName}'
  location: location
  tags: tags
}

// ============================================================
// Tier 1: Independent modules (deploy in parallel)
// ============================================================

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    resourceToken: resourceToken
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    resourceToken: resourceToken
  }
}

// cognitive module now includes: AI Services account + Project + Connection + gpt-4o
module cognitive 'modules/cognitive.bicep' = {
  name: 'cognitive'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    accountSuffix: cognitiveAccountSuffix
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

// Entra ID App Registrations
// Created by postprovision hook (az CLI with delegated permissions) — the Graph
// Bicep extension requires Application.ReadWrite.All on the ARM deployment
// identity, which is not available in managed tenants.

// ============================================================
// Tier 1.5: Modules with Tier 1 dependencies (monitoring only)
// ============================================================

module workbook 'modules/workbook.bicep' = {
  name: 'workbook'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    appInsightsId: monitoring.outputs.appInsightsId
  }
}

// ============================================================
// Tier 2: Container Apps Environment (needs monitoring)
// ============================================================

module containerEnv 'modules/container-env.bicep' = {
  name: 'container-env'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
}

// ============================================================
// Tier 2.5: Chat App (needs registry + container environment + cognitive)
// ============================================================

module chatApp 'modules/chat-app.bicep' = {
  name: 'chat-app'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    registryLoginServer: registry.outputs.registryLoginServer
    registryName: registry.outputs.registryName
    containerAppsEnvironmentId: containerEnv.outputs.containerAppsEnvironmentId
    projectEndpoint: cognitive.outputs.projectEndpoint
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

// ============================================================
// Tier 2.5b: Salesforce MCP App (needs registry + container environment)
// ============================================================

module sfMcpApp 'modules/salesforce-mcp-app.bicep' = {
  name: 'salesforce-mcp-app'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    registryLoginServer: registry.outputs.registryLoginServer
    registryName: registry.outputs.registryName
    containerAppsEnvironmentId: containerEnv.outputs.containerAppsEnvironmentId
    sfInstanceUrl: sfInstanceUrl
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

// ============================================================
// Tier 3: APIM (needs cognitive + monitoring)
// ============================================================

module apim 'modules/apim.bicep' = {
  name: 'apim'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    cognitiveEndpoint: cognitive.outputs.openaiEndpoint
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
}

// ============================================================
// Tier 3.5: Salesforce MCP OBO APIM Proxy (depends on APIM + SF MCP Container App)
// ============================================================

module apimSfMcpObo 'modules/apim-sf-mcp-obo.bicep' = {
  name: 'apim-sf-mcp-obo'
  scope: rg
  params: {
    apimName: apim.outputs.apimName
    sfMcpFqdn: sfMcpApp.outputs.sfMcpFqdn
    tenantId: subscription().tenantId
    sfOboClientId: sfConnectedAppClientId
    sfOboLoginUrl: !empty(sfInstanceUrl) ? sfInstanceUrl : 'https://login.salesforce.com'
    sfJwtBearerCertThumbprint: sfJwtBearerCertThumbprint
    sfServiceAccountUsername: sfServiceAccountUsername
    identityClaimName: identityClaimName
  }
}

// ============================================================
// Tier 4: Modules with Tier 3 dependencies
// ============================================================

module apimCognitiveRoleAssignment 'modules/role-assignment.bicep' = {
  name: 'apim-cognitive-role'
  scope: rg
  params: {
    cognitiveAccountName: cognitive.outputs.cognitiveAccountName
    principalId: apim.outputs.apimPrincipalId
  }
}

module aiGatewayConnection 'modules/ai-gateway-connection.bicep' = {
  name: 'ai-gateway-connection'
  scope: rg
  params: {
    cognitiveAccountName: cognitive.outputs.cognitiveAccountName
    projectName: cognitive.outputs.projectName
    apimGatewayUrl: apim.outputs.apimGatewayUrl
    apimResourceId: apim.outputs.apimResourceId
  }
}

module chatAppCognitiveRole 'modules/role-assignment.bicep' = {
  name: 'chat-app-cognitive-role'
  scope: rg
  params: {
    cognitiveAccountName: cognitive.outputs.cognitiveAccountName
    principalId: chatApp.outputs.chatAppPrincipalId
  }
}

module chatAppCognitiveContributor 'modules/role-assignment.bicep' = {
  name: 'chat-app-cognitive-contributor'
  scope: rg
  params: {
    cognitiveAccountName: cognitive.outputs.cognitiveAccountName
    principalId: chatApp.outputs.chatAppPrincipalId
    roleDefinitionId: '25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68' // Cognitive Services Contributor
  }
}

// OBO connection: passes Azure AD token through to APIM
module sfOboConnection 'modules/sf-obo-connection.bicep' = {
  name: 'sf-obo-connection'
  scope: rg
  params: {
    cognitiveAccountName: cognitive.outputs.cognitiveAccountName
    projectName: cognitive.outputs.projectName
    sfMcpOboEndpoint: apimSfMcpObo.outputs.sfMcpOboEndpoint
  }
}

// Grant APIM access to Key Vault certificates for JWT signing
module keyvaultApimAccess 'modules/keyvault.bicep' = {
  name: 'keyvault-apim-access'
  scope: rg
  params: {
    name: baseName
    location: location
    tags: tags
    apimPrincipalId: apim.outputs.apimPrincipalId
  }
}

// ============================================================
// Tier 4.5: APIM JWT Bearer Certificate (after KV RBAC grant)
// Must run AFTER keyvaultApimAccess so APIM has "Key Vault Secrets User" role.
// ============================================================

module apimJwtBearerCert 'modules/apim-jwt-bearer-cert.bicep' = {
  name: 'apim-jwt-bearer-cert'
  scope: rg
  params: {
    apimName: apim.outputs.apimName
    keyVaultUri: keyvault.outputs.keyVaultUri
    sfJwtBearerCertName: sfJwtBearerCertName
  }
  dependsOn: [ keyvaultApimAccess ]
}

// ============================================================
// Outputs (become azd env vars)
// ============================================================

output AZURE_CONTAINER_REGISTRY_NAME string = registry.outputs.registryName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.registryLoginServer
output APIM_GATEWAY_URL string = apim.outputs.apimGatewayUrl
output APIM_NAME string = apim.outputs.apimName
output AI_FOUNDRY_PROJECT_NAME string = cognitive.outputs.projectName
output AI_FOUNDRY_PROJECT_ENDPOINT string = cognitive.outputs.projectEndpoint
output APIM_OPENAI_ENDPOINT string = '${apim.outputs.apimGatewayUrl}/openai'
output COGNITIVE_ACCOUNT_NAME string = cognitive.outputs.cognitiveAccountName
output AZURE_RESOURCE_GROUP string = rg.name
output CHAT_APP_URL string = 'https://${chatApp.outputs.chatAppFqdn}'
output CHAT_APP_FQDN string = chatApp.outputs.chatAppFqdn
output CHAT_APP_CONTAINER_APP_NAME string = chatApp.outputs.chatAppName
output SF_MCP_CONTAINER_APP_NAME string = sfMcpApp.outputs.sfMcpAppName
output SF_MCP_FQDN string = sfMcpApp.outputs.sfMcpFqdn
output SF_OBO_CONNECTION_NAME string = sfOboConnection.outputs.connectionName
output APIM_SF_MCP_OBO_ENDPOINT string = apimSfMcpObo.outputs.sfMcpOboEndpoint
output KEY_VAULT_NAME string = keyvault.outputs.keyVaultName
// CHAT_APP_ENTRA_CLIENT_ID set by postprovision hook
// (Entra apps created via az CLI, not Bicep)
