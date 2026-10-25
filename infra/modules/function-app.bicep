param name string
param location string
param tags object = {}
param planResourceId string
param identityResourceId string
param identityClientId string
param storageAccountName string
param deploymentContainerName string
param subnetResourceId string
param applicationInsightsName string
param appSettings object

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

var settings = union(appSettings, {
  AzureWebJobsStorage__credential: 'managedidentity'
  AzureWebJobsStorage__clientId: identityClientId
  AzureWebJobsStorage__blobServiceUri: storage.properties.primaryEndpoints.blob
  AzureWebJobsStorage__queueServiceUri: storage.properties.primaryEndpoints.queue
  APPLICATIONINSIGHTS_CONNECTION_STRING: insights.properties.ConnectionString
  APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'ClientId=${identityClientId};Authorization=AAD'
})

module functionApp 'br/public:avm/res/web/site:0.15.1' = {
  name: 'flex-function'
  params: {
    name: name
    kind: 'functionapp,linux'
    location: location
    tags: tags
    serverFarmResourceId: planResourceId
    managedIdentities: {
      userAssignedResourceIds: [identityResourceId]
    }
    virtualNetworkSubnetId: subnetResourceId
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: identityResourceId
          }
        }
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 2048
        maximumInstanceCount: 5
      }
      runtime: {
        name: 'python'
        version: '3.12'
      }
    }
    siteConfig: { alwaysOn: false }
    appSettingsKeyValuePairs: settings
  }
}

output name string = functionApp.outputs.name

