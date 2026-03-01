@description('Base name for resources')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('APIM managed identity principal ID (for Key Vault certificate access)')
param apimPrincipalId string = ''

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${name}'
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// Grant APIM "Key Vault Secrets User" role so it can read certificates.
// Certificates in Key Vault are accessed as secrets (the PFX/PEM bundle).
// Role: Key Vault Secrets User (4633458b-17de-408a-b874-0445c86b69e6)
resource apimKvSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(apimPrincipalId)) {
  name: guid(keyVault.id, apimPrincipalId, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: apimPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
