targetScope = 'subscription'

@minLength(1)
@maxLength(30)
param environmentName string

@description('Azure region that supports Flex Consumption.')
param location string

@description('Object ID used by azd to upload the deployment package.')
param deployerPrincipalId string = deployer().objectId

var token = take(toLower(uniqueString(subscription().id, environmentName, location)), 10)
var tags = {
  project: 'velib-datalake'
  environment: environmentName
}
var functionName = 'func-velib-${token}'
var storageName = 'stvelib${token}'
var deploymentContainerName = 'app-package'
var rawContainerName = 'velib-raw'

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-velib-${environmentName}'
  location: location
  tags: tags
}

module identity 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.1' = {
  name: 'collector-identity'
  scope: resourceGroup
  params: {
    name: 'id-velib-${token}'
    location: location
    tags: tags
  }
}

module plan 'br/public:avm/res/web/serverfarm:0.1.1' = {
  name: 'flex-plan'
  scope: resourceGroup
  params: {
    name: 'plan-velib-${token}'
    location: location
    reserved: true
    sku: {
      name: 'FC1'
      tier: 'FlexConsumption'
    }
    tags: tags
  }
}

module network './modules/network.bicep' = {
  name: 'network'
  scope: resourceGroup
  params: {
    name: 'vnet-velib-${token}'
    location: location
    tags: tags
  }
}

module storage 'br/public:avm/res/storage/storage-account:0.8.3' = {
  name: 'storage'
  scope: resourceGroup
  params: {
    name: storageName
    location: location
    tags: tags
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
    minimumTlsVersion: 'TLS1_2'
    blobServices: {
      containers: [
        { name: deploymentContainerName }
        { name: rawContainerName }
      ]
    }
  }
}

module privateEndpoints './modules/storage-private-endpoints.bicep' = {
  name: 'storage-private-endpoints'
  scope: resourceGroup
  params: {
    location: location
    storageAccountName: storage.outputs.name
    virtualNetworkName: network.outputs.name
    privateEndpointSubnetName: network.outputs.privateEndpointSubnetName
    tags: tags
  }
}

module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.11.1' = {
  name: 'logs'
  scope: resourceGroup
  params: {
    name: 'log-velib-${token}'
    location: location
    dataRetention: 30
    tags: tags
  }
}

module applicationInsights 'br/public:avm/res/insights/component:0.6.0' = {
  name: 'application-insights'
  scope: resourceGroup
  params: {
    name: 'appi-velib-${token}'
    location: location
    workspaceResourceId: logAnalytics.outputs.resourceId
    disableLocalAuth: true
    tags: tags
  }
}

module roles './modules/rbac.bicep' = {
  name: 'roles'
  scope: resourceGroup
  params: {
    storageAccountName: storage.outputs.name
    applicationInsightsName: applicationInsights.outputs.name
    runtimePrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module functionApp './modules/function-app.bicep' = {
  name: 'function-app'
  scope: resourceGroup
  dependsOn: [roles, privateEndpoints]
  params: {
    name: functionName
    location: location
    tags: union(tags, { 'azd-service-name': 'collector' })
    planResourceId: plan.outputs.resourceId
    identityResourceId: identity.outputs.resourceId
    identityClientId: identity.outputs.clientId
    storageAccountName: storage.outputs.name
    deploymentContainerName: deploymentContainerName
    subnetResourceId: network.outputs.functionSubnetResourceId
    applicationInsightsName: applicationInsights.outputs.name
    appSettings: {
      TIMER_SCHEDULE: '0 */5 * * * *'
      VELIB_STORAGE_BACKEND: 'azure'
      VELIB_STORAGE_ACCOUNT_URL: 'https://${storage.outputs.name}.blob.${environment().suffixes.storage}'
      VELIB_STORAGE_CONTAINER: rawContainerName
      VELIB_MIN_RECORDS: '1000'
      VELIB_FRESHNESS_MINUTES: '45'
      AZURE_CLIENT_ID: identity.outputs.clientId
      PYTHONPATH: '/home/site/wwwroot/src'
    }
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_FUNCTION_NAME string = functionApp.outputs.name
output SERVICE_COLLECTOR_NAME string = functionApp.outputs.name
output RAW_STORAGE_ACCOUNT_NAME string = storage.outputs.name

