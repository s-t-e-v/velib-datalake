param name string
param location string
param tags object = {}

var privateEndpointSubnetName = 'private-endpoints'
var functionSubnetName = 'functions'

module virtualNetwork 'br/public:avm/res/network/virtual-network:0.6.1' = {
  name: 'virtual-network'
  params: {
    name: name
    location: location
    tags: tags
    addressPrefixes: ['10.20.0.0/16']
    subnets: [
      {
        name: privateEndpointSubnetName
        addressPrefix: '10.20.1.0/24'
        privateEndpointNetworkPolicies: 'Disabled'
      }
      {
        name: functionSubnetName
        addressPrefix: '10.20.2.0/24'
        delegation: 'Microsoft.App/environments'
      }
    ]
  }
}

output name string = name
output privateEndpointSubnetName string = privateEndpointSubnetName
output functionSubnetResourceId string = '${virtualNetwork.outputs.resourceId}/subnets/${functionSubnetName}'

