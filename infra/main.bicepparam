using './main.bicep'

param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'sf-mcp-tool')
param location = readEnvironmentVariable('AZURE_LOCATION', 'swedencentral')
param cognitiveAccountSuffix = readEnvironmentVariable('COGNITIVE_ACCOUNT_SUFFIX', '')
param sfInstanceUrl = readEnvironmentVariable('SF_INSTANCE_URL', '')
param sfConnectedAppClientId = readEnvironmentVariable('SF_CONNECTED_APP_CLIENT_ID', 'placeholder-updated-by-hook')
param sfConnectedAppClientSecret = readEnvironmentVariable('SF_CONNECTED_APP_CLIENT_SECRET', '')
