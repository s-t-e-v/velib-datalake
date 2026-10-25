targetScope = 'subscription'

@minLength(1)
@maxLength(30)
param environmentName string

@description('Azure region for the resource group and VM stack.')
param location string = 'francecentral'

@description('SSH public key for azureuser. Password authentication is disabled.')
@minLength(1)
param sshPublicKey string

@description('Object ID of the deploying user, granted Storage Blob Data Contributor.')
@minLength(36)
@maxLength(36)
param deployerPrincipalId string

@description('Caller-supplied IPv4 CIDR allowed to connect over SSH, for example 203.0.113.10/32. Use the narrowest possible range.')
@minLength(9)
@maxLength(18)
param sshAllowedCidr string

@description('Azure VM size. Availability and quota depend on the subscription and region.')
param vmSize string = 'Standard_B2ats_v2'

var token = take(toLower(uniqueString(subscription().id, environmentName, location)), 10)
var tags = {
  project: 'velib-datalake'
  environment: environmentName
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-velib-${environmentName}'
  location: location
  tags: tags
}

module vmStack './modules/vm-stack.bicep' = {
  name: 'vm-stack'
  scope: resourceGroup
  params: {
    token: token
    location: location
    tags: tags
    sshPublicKey: sshPublicKey
    deployerPrincipalId: deployerPrincipalId
    sshAllowedCidr: sshAllowedCidr
    vmSize: vmSize
  }
}

output VM_PUBLIC_IP string = vmStack.outputs.VM_PUBLIC_IP
output VM_NAME string = vmStack.outputs.VM_NAME
output RESOURCE_GROUP string = resourceGroup.name
output RAW_STORAGE_ACCOUNT_NAME string = vmStack.outputs.RAW_STORAGE_ACCOUNT_NAME
output RAW_STORAGE_ACCOUNT_URL string = vmStack.outputs.RAW_STORAGE_ACCOUNT_URL
