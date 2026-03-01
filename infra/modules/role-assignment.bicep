@description('Name of the Cognitive Services account to scope the role to')
param cognitiveAccountName string

@description('Principal ID to assign the role to (e.g. APIM managed identity)')
param principalId string

@description('Role definition ID (GUID only)')
param roleDefinitionId string = 'a97b65f3-24c7-4388-baec-2e87135dc908' // Cognitive Services User

@description('Principal type')
param principalType string = 'ServicePrincipal'

resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: cognitiveAccountName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cognitiveAccount.id, principalId, roleDefinitionId)
  scope: cognitiveAccount
  properties: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalType: principalType
  }
}
