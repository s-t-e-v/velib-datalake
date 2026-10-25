param storageAccountName string
param applicationInsightsName string
param runtimePrincipalId string
param deployerPrincipalId string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

var blobOwner = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
)
var queueContributor = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
)
var monitoringPublisher = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)

// Blob Data Owner is required by identity-based AzureWebJobsStorage for timer coordination.
resource runtimeBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, runtimePrincipalId, blobOwner)
  scope: storage
  properties: {
    roleDefinitionId: blobOwner
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource runtimeQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, runtimePrincipalId, queueContributor)
  scope: storage
  properties: {
    roleDefinitionId: queueContributor
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, deployerPrincipalId, blobOwner)
  scope: storage
  properties: {
    roleDefinitionId: blobOwner
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource runtimeMonitoring 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insights.id, runtimePrincipalId, monitoringPublisher)
  scope: insights
  properties: {
    roleDefinitionId: monitoringPublisher
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
  }
}

