@description('Base name for resources')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('Log Analytics Workspace resource ID')
param logAnalyticsWorkspaceId string

@description('Application Insights resource ID')
param appInsightsId string

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, name, 'identity-propagation')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: 'Identity Propagation Dashboard'
    category: 'workbook'
    serializedData: loadTextContent('../workbooks/identity-propagation.json')
    sourceId: appInsightsId
  }
}

output workbookId string = workbook.id
