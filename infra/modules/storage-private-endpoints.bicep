param location string
param storageAccountName string
param virtualNetworkName string
param privateEndpointSubnetName string
param tags object = {}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: virtualNetworkName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

var services = [
  {
    group: 'blob'
    zone: 'privatelink.blob.${environment().suffixes.storage}'
  }
  {
    group: 'queue'
    zone: 'privatelink.queue.${environment().suffixes.storage}'
  }
]

resource privateDnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for service in services: {
  name: service.zone
  location: 'global'
  tags: tags
}]

resource dnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (service, index) in services: {
  parent: privateDnsZones[index]
  name: '${storageAccountName}-${service.group}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: virtualNetwork.id }
  }
}]

resource privateEndpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [for service in services: {
  name: 'pe-${storageAccountName}-${service.group}'
  location: location
  tags: tags
  properties: {
    subnet: { id: '${virtualNetwork.id}/subnets/${privateEndpointSubnetName}' }
    privateLinkServiceConnections: [
      {
        name: '${service.group}-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [service.group]
        }
      }
    ]
  }
}]

resource dnsGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (service, index) in services: {
  parent: privateEndpoints[index]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: service.group
        properties: { privateDnsZoneId: privateDnsZones[index].id }
      }
    ]
  }
  dependsOn: [dnsLinks[index]]
}]

