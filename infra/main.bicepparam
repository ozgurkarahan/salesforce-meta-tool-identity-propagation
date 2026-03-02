using './main.bicep'

param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'sf-mcp-tool')
param location = readEnvironmentVariable('AZURE_LOCATION', 'swedencentral')
param cognitiveAccountSuffix = readEnvironmentVariable('COGNITIVE_ACCOUNT_SUFFIX', '')
param sfInstanceUrl = readEnvironmentVariable('SF_INSTANCE_URL', '')
param sfConnectedAppClientId = readEnvironmentVariable('SF_CONNECTED_APP_CLIENT_ID', 'placeholder-updated-by-hook')
param sfJwtBearerCertName = readEnvironmentVariable('SF_JWT_BEARER_CERT_NAME', 'sf-jwt-bearer')
param sfJwtBearerCertThumbprint = readEnvironmentVariable('SF_JWT_BEARER_CERT_THUMBPRINT', '')
param sfServiceAccountUsername = readEnvironmentVariable('SF_SERVICE_ACCOUNT_USERNAME', '')
param identityClaimName = readEnvironmentVariable('IDENTITY_CLAIM_NAME', 'oid')
