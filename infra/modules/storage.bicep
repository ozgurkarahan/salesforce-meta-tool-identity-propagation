@description('Base name for resources')
@minLength(1)
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('Unique suffix for globally unique names')
param resourceToken string

var storageName = take('st${replace(name, '-', '')}${resourceToken}', 24)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

output storageAccountId string = storage.id
output storageAccountName string = storage.name
