import re

from charmhelpers.core import hookenv

from spcharms import config as spconfig


def read_interfaces(rdebug):
    rdebug('trying to parse the system interface configuration')
    hookenv.status_set('maintenance', 'parsing the system interface configuration')
    blocks = []
    interfaces = {}
    re_i = {
        'auto': re.compile('\s* auto \s+ (?P<iface> \S+ ) \s* $', re.X),
        'empty': re.compile('\s* $', re.X),
        'iface': re.compile('\s* iface \s+ (?P<iface> \S+ ) \s+ inet \s+ (?P<inet> \S+ ) $', re.X),
        'ifprop': re.compile('\s* (?P<var> \S+ ) \s+ (?P<value> .* ) $', re.X),
        'source': re.compile('\s* source \s+ (?P<path> \S+ ) \s* $', re.X),
    }
    with open('/etc/network/interfaces', 'r') as f:
        iface = None
        empty = ''
        nonempty = ''
        for nline in f.readlines():
            line = nline.rstrip()
            if iface is None:
                found = None
                for tp in ('auto', 'iface', 'source', 'empty'):
                    m = re_i[tp].match(line)
                    if m:
                        found = tp
                        data = m.groupdict()
                        break
                defblock = {
                    'type': found,
                    'empty': empty,
                    'data': data,
                    'lines': nline,
                }
                if found is None:
                    rdebug('Unexpected /etc/network/interfaces line: {line}'.format(line=line))
                    hookenv.status_set('error', 'Could not parse /etc/network/interfaces: unexpected line: {line}'.format(line=line))
                    return
                elif found == 'empty':
                    empty = empty + nline
                elif found == 'source':
                    blocks.append(defblock)
                    empty = ''
                elif found == 'auto':
                    if data['iface'] in interfaces:
                        interfaces[data['iface']]['auto'] = True
                    else:
                        interfaces[data['iface']] = {
                            'auto': True,
                            'data': None,
                        }
                    blocks.append(defblock)
                    empty = ''
                elif found == 'iface':
                    if data['iface'] not in interfaces:
                        interfaces[data['iface']] = {
                            'auto': False,
                            'data': None,
                        }
                    if interfaces[data['iface']]['data'] is not None:
                        rdebug('duplicate interface definition for {iface}'.format(iface=data['iface']))
                        hookenv.status_set('error', 'Duplicate interface definition for {iface} in /etc/network/interfaces'.format(iface=data['iface']))
                        return
                    interfaces[data['iface']]['data'] = {}
                    iface = data['iface']
                    nonempty = nline
                else:
                    rdebug('FIXME: grrr, handle the {t} type!'.format(t=found))
            else:
                if re_i['empty'].match(line):
                    rdebug('done with the definition of the {iface} iface, it seems'.format(iface=iface))
                    blocks.append({
                        'type': 'iface',
                        'name': iface,
                        'empty': empty,
                        'data': interfaces[iface]['data'],
                        'lines': nonempty,
                    })
                    empty = nline
                    iface = None
                    continue
                m = re_i['ifprop'].match(line)
                if not m:
                    rdebug('invalid interface property line for the {iface} interface: {line}'.format(iface=iface, line=line))
                    hookenv.status_set('error', 'invalid interface property line for the {iface} interface: {line}'.format(iface=iface, line=line))
                    return
                d = m.groupdict()
                rdebug('got an interface property: "{var}": "{value}"'.format(var=d['var'], value=d['value']))
                ifd = interfaces[iface]['data']
                if d['var'].startswith('pre-') or d['var'].startswith('post-'):
                    if d['var'] not in ifd:
                        ifd[d['var']] = []
                    ifd[d['var']].append(d['value'])
                else:
                    ifd[d['var']] = d['value']

                nonempty += nline

    if iface is not None:
        rdebug('fallen off EOF with the definition of the {iface} iface, it seems'.format(iface=iface))
        blocks.append({
            'type': 'iface',
            'name': iface,
            'empty': empty,
            'data': interfaces[iface]['data'],
            'lines': nonempty,
        })
        iface = None

    rdebug('done with the /etc/network/interfaces file')
    return { 'blocks': blocks, 'interfaces': interfaces, 'changed': False, 'changed-interfaces': set(), }


def get_spiface_network(iface):
    cfg = spconfig.get_dict()
    nets = dict(map(
        lambda s: s.split('=', 1),
        cfg['SP_IFACE_NETWORKS'].split(',')
    ))
    return nets[iface]

vlandef = {
    'post-up': [
        '/sbin/ip link set dev ${IF_VLAN_RAW_DEVICE} mtu 9000',
        '/sbin/ip link set dev ${IFACE} mtu 9000',
    ],
}
nonvlandef = {
    'post-up': [
        '/sbin/ip link set dev ${IFACE} mtu 9000',
        '/sbin/ethtool -A ${IFACE} autoneg off tx off rx on || true',
        '/sbin/ethtool -C ${IFACE} rx-usecs 16 || true',
        '/sbin/ethtool -G ${IFACE} rx 4096 tx 512 || true',
    ],
}


def build_interface_lines(iface, data):
    res = 'iface ' + iface + ' inet static\n';
    for var in sorted(data.keys()):
        value = data[var]
        if var.startswith('pre-') or var.startswith('post-'):
            res += ''.join(map(lambda v: '  ' + var + ' ' + v + '\n', value))
        else:
            res += '  ' + var + ' ' + value + '\n'
    return res
        

def build_vlan_data(iface, parent, cfg):
    data = {
        'address': get_spiface_network(iface) + cfg['SP_OURID'],
        'netmask': '255.255.255.0',
        'mtu': '9000',
        'vlan-raw-device': parent,
    }

    global vlandef
    data.update(vlandef)

    return data


def build_nonvlan_data(iface, cfg):
    data = {
        'address': get_spiface_network(iface) + cfg['SP_OURID'],
        'netmask': '255.255.255.0',
        'mtu': '9000',
    }

    global nonvlandef
    data.update(nonvlandef)

    return data


def update_interface_if_needed(ifdata, iface, rdebug):
    rdebug('updating the {iface} interface if needed'.format(iface=iface))
    cfg = spconfig.get_dict()
    if iface.find('.') != -1:
        parent = iface.split('.', 1)[0]
        update_interface_if_needed(ifdata, parent, rdebug)
        rdebug('back to updating the {iface} interface if needed'.format(iface=iface))

        data = build_vlan_data(iface, parent, cfg)
    else:
        data = build_nonvlan_data(iface, cfg)
        
    ifd = ifdata['interfaces'][iface]['data']
    changed = False
    for var in data:
        wanted = data[var]

        if var not in ifd:
            ifd[var] = wanted
            changed = True
            continue
        current = ifd[var]

        if var.startswith('pre-') or var.startswith('post-'):
            for line in wanted:
                if not line in current:
                    current.append(line)
                    changed = True
        else:
            if current != wanted:
                ifd[var] = wanted
                changed = True
    
    if not ifdata['interfaces'][iface]['auto']:
        changed = True

    if changed:
        ifdata['changed'] = True
        ifdata['changed-interfaces'].add(iface)
        idx = -1
        for i in range(len(ifdata['blocks'])):
            bl = ifdata['blocks'][i]
            if bl['type'] == 'iface' and bl['name'] == iface:
                bl['lines'] = build_interface_lines(iface, ifdata['interfaces'][iface]['data'])
                rdebug('something changed, do we have the correct block for {iface}? {bl}'.format(iface=iface, bl=bl))
                idx = i
                break
        if idx == -1:
            raise Exception('storpool-config internal error: could not find a block defining the {iface} interface'.format(iface=iface))
        if not ifdata['interfaces'][iface]['auto']:
            ifdata['interfaces'][iface]['auto'] = True
            ifdata['blocks'].insert(idx, {
                'type': 'auto',
                'empty': '\n',
                'data': {'iface': iface},
                'lines': 'auto ' + iface + '\n',
            })
    else:
        rdebug('nothing seems to have changed for {iface}'.format(iface=iface))


def add_interface(ifdata, iface, rdebug):
    rdebug('adding interface {iface}'.format(iface=iface))
    cfg = spconfig.get_dict()

    if iface.find('.') != -1:
        parent = iface.split('.', 1)[0]
        update_interface_if_needed(ifdata, parent, rdebug)

        rdebug('back to adding interface {iface}'.format(iface=iface))
        data = build_vlan_data(iface, parent, cfg)
    else:
        data = build_nonvlan_data(iface, cfg)

    ifdata['interfaces'][iface] = {
        'auto': True,
        'data': data,
    }

    ifdata['blocks'].append({
        'type': 'auto',
        'empty': '\n',
        'data': {'iface': iface},
        'lines': 'auto ' + iface + '\n',
    })
    rdebug('whee, did we get the right auto data? {bl}'.format(bl=ifdata['blocks'][-1]))
    ifdata['blocks'].append({
        'type': 'iface',
        'empty': '',
        'data': data,
        'lines': build_interface_lines(iface, data),
    })
    rdebug('whee, did we get the right data? {bl}'.format(bl=ifdata['blocks'][-1]))

    ifdata['changed'] = True
    ifdata['changed-interfaces'].add(iface)


def write_interfaces(ifdata, fname, rdebug):
    rdebug('writing out the interface definitions to {fname}'.format(fname=fname))
    with open(fname, 'w') as f:
        for bl in ifdata['blocks']:
            print(bl['empty'], end='', file=f)
            print(bl['lines'], end='', file=f)
