targetScope = 'resourceGroup'

param token string
param location string
param tags object
param sshPublicKey string
param deployerPrincipalId string
param sshAllowedCidr string
param vmSize string
param adminUsername string = 'azureuser'

var vmName = 'vm-velib-${token}'
var blobDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stvelib${token}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'None'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource rawContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'velib-raw'
  properties: {
    publicAccess: 'None'
  }
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-velib-${token}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowSshFromCaller'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '22'
          sourceAddressPrefix: sshAllowedCidr
          destinationAddressPrefix: '*'
        }
      }
      {
        // Override the default AllowVnetInBound and AllowAzureLoadBalancerInBound rules.
        name: 'DenyOtherInbound'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-velib-${token}'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']
    }
    subnets: [
      {
        name: 'vm'
        properties: {
          addressPrefix: '10.0.0.0/24'
          networkSecurityGroup: {
            id: nsg.id
          }
        }
      }
    ]
  }
}

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-velib-${token}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-velib-${token}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'primary'
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: '${vnet.id}/subnets/vm'
          }
          publicIPAddress: {
            id: publicIp.id
          }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 32
        deleteOption: 'Delete'
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
          properties: {
            primary: true
          }
        }
      ]
    }
  }
}

resource vmBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, vm.id, blobDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: blobDataContributorRoleId
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, deployerPrincipalId, blobDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: blobDataContributorRoleId
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

output VM_PUBLIC_IP string = publicIp.properties.ipAddress
output VM_NAME string = vm.name
output RAW_STORAGE_ACCOUNT_NAME string = storage.name
output RAW_STORAGE_ACCOUNT_URL string = storage.properties.primaryEndpoints.blob
