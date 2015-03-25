# vim: set ts=4:et
import os
import uuid
import socket
from charmhelpers.core.hookenv import (
    config,
    relation_ids,
    related_units,
    relation_get,
    unit_get,
    cached
)
from charmhelpers.fetch import (
    apt_install,
)
from charmhelpers.contrib.openstack.context import (
    OSContextGenerator,
    context_complete,
    NeutronPortContext,
    NeutronAPIContext,
)
from charmhelpers.contrib.openstack.utils import (
    get_os_codename_install_source
)
from charmhelpers.contrib.hahelpers.cluster import(
    eligible_leader
)
from charmhelpers.contrib.network.ip import (
    get_address_in_network,
)
from charmhelpers.contrib.openstack.neutron import (
    parse_data_port_mappings,
    parse_vlan_range_mappings,
)
from charmhelpers.core.host import (
    get_nic_hwaddr,
)
from charmhelpers.core.strutils import bool_from_string
import copy

DB_USER = "quantum"
QUANTUM_DB = "quantum"
NOVA_DB_USER = "nova"
NOVA_DB = "nova"

QUANTUM_OVS_PLUGIN = \
    "quantum.plugins.openvswitch.ovs_quantum_plugin.OVSQuantumPluginV2"
QUANTUM_NVP_PLUGIN = \
    "quantum.plugins.nicira.nicira_nvp_plugin.QuantumPlugin.NvpPluginV2"
NEUTRON_OVS_PLUGIN = \
    "neutron.plugins.openvswitch.ovs_neutron_plugin.OVSNeutronPluginV2"
NEUTRON_ML2_PLUGIN = \
    "neutron.plugins.ml2.plugin.Ml2Plugin"
NEUTRON_NVP_PLUGIN = \
    "neutron.plugins.nicira.nicira_nvp_plugin.NeutronPlugin.NvpPluginV2"
NEUTRON_N1KV_PLUGIN = \
    "neutron.plugins.cisco.n1kv.n1kv_neutron_plugin.N1kvNeutronPluginV2"
NEUTRON_NSX_PLUGIN = "vmware"

NEUTRON = 'neutron'
QUANTUM = 'quantum'


def networking_name():
    ''' Determine whether neutron or quantum should be used for name '''
    if get_os_codename_install_source(config('openstack-origin')) >= 'havana':
        return NEUTRON
    else:
        return QUANTUM

OVS = 'ovs'
NVP = 'nvp'
N1KV = 'n1kv'
NSX = 'nsx'

CORE_PLUGIN = {
    QUANTUM: {
        OVS: QUANTUM_OVS_PLUGIN,
        NVP: QUANTUM_NVP_PLUGIN,
    },
    NEUTRON: {
        OVS: NEUTRON_OVS_PLUGIN,
        NVP: NEUTRON_NVP_PLUGIN,
        N1KV: NEUTRON_N1KV_PLUGIN,
        NSX: NEUTRON_NSX_PLUGIN
    },
}


def remap_plugin(plugin):
    ''' Remaps plugin name for renames/switches in packaging '''
    release = get_os_codename_install_source(config('openstack-origin'))
    if plugin == 'nvp' and release >= 'icehouse':
        plugin = 'nsx'
    elif plugin == 'nsx' and release < 'icehouse':
        plugin = 'nvp'
    return plugin


def core_plugin():
    plugin = remap_plugin(config('plugin'))
    if (get_os_codename_install_source(config('openstack-origin'))
            >= 'icehouse'
            and plugin == OVS):
        return NEUTRON_ML2_PLUGIN
    else:
        return CORE_PLUGIN[networking_name()][plugin]


class L3AgentContext(OSContextGenerator):

    def __call__(self):
        api_settings = NeutronAPIContext()()
        ctxt = {}
        if config('run-internal-router') == 'leader':
            ctxt['handle_internal_only_router'] = eligible_leader(None)

        if config('run-internal-router') == 'all':
            ctxt['handle_internal_only_router'] = True

        if config('run-internal-router') == 'none':
            ctxt['handle_internal_only_router'] = False

        if config('external-network-id'):
            ctxt['ext_net_id'] = config('external-network-id')
        if config('plugin'):
            ctxt['plugin'] = config('plugin')
        if api_settings['enable_dvr']:
            ctxt['agent_mode'] = 'dvr_snat'
        else:
            ctxt['agent_mode'] = 'legacy'
        return ctxt


class QuantumGatewayContext(OSContextGenerator):

    def __call__(self):
        api_settings = NeutronAPIContext()()
        ctxt = {
            'shared_secret': get_shared_secret(),
            'local_ip':
            get_address_in_network(config('os-data-network'),
                                   get_host_ip(unit_get('private-address'))),
            'core_plugin': core_plugin(),
            'plugin': config('plugin'),
            'debug': config('debug'),
            'verbose': config('verbose'),
            'instance_mtu': config('instance-mtu'),
            'l2_population': api_settings['l2_population'],
            'enable_dvr': api_settings['enable_dvr'],
            'enable_l3ha': api_settings['enable_l3ha'],
            'overlay_network_type':
            api_settings['overlay_network_type'],
        }

        mappings = config('bridge-mappings')
        if mappings:
            ctxt['bridge_mappings'] = mappings

        vlan_ranges = config('vlan-ranges')
        vlan_range_mappings = parse_vlan_range_mappings(vlan_ranges)
        if vlan_range_mappings:
            providers = sorted(vlan_range_mappings.keys())
            ctxt['network_providers'] = ' '.join(providers)
            ctxt['vlan_ranges'] = vlan_ranges

        net_dev_mtu = api_settings['network_device_mtu']
        if net_dev_mtu:
            ctxt['network_device_mtu'] = net_dev_mtu
            ctxt['veth_mtu'] = net_dev_mtu

        return ctxt


@cached
def get_host_ip(hostname=None):
    try:
        import dns.resolver
    except ImportError:
        apt_install('python-dnspython', fatal=True)
        import dns.resolver
    hostname = hostname or unit_get('private-address')
    try:
        # Test to see if already an IPv4 address
        socket.inet_aton(hostname)
        return hostname
    except socket.error:
        answers = dns.resolver.query(hostname, 'A')
        if answers:
            return answers[0].address


SHARED_SECRET = "/etc/{}/secret.txt"


def get_shared_secret():
    secret = None
    _path = SHARED_SECRET.format(networking_name())
    if not os.path.exists(_path):
        secret = str(uuid.uuid4())
        with open(_path, 'w') as secret_file:
            secret_file.write(secret)
    else:
        with open(_path, 'r') as secret_file:
            secret = secret_file.read().strip()
    return secret
