"""
A StorPool Juju charm helper module for parsing and updating the Ubuntu
network interface configuration if needed.
"""
import glob
import os
import tempfile

from spcharms import txn
from spcharms import utils as sputils

vlandef = [
    'post-up /sbin/ip link set dev {IF_VLAN_RAW_DEVICE} mtu {MTU}',
    'post-up /sbin/ip link set dev {IFACE} mtu {MTU}',
]
nonvlandef = [
    'post-up /sbin/ip link set dev {IFACE} mtu {MTU}',
    'post-up /sbin/ethtool -A {IFACE} autoneg off tx off rx on || true',
    'post-up /sbin/ethtool -C {IFACE} rx-usecs 16 || true',
    'post-up /sbin/ethtool -G {IFACE} rx 4096 tx 512 || true',
]


def rdebug(s):
    """
    Pass the diagnostic message string `s` to the central diagnostic logger.
    """
    sputils.rdebug(s, prefix='config')


def fixup_interfaces_file(fname, data, handled):
    """
    Read an /etc/network/interfaces-like file, look for the interfaces
    listed in the `data` dictionary.  If any of them are found, check that
    they have all of the lines defined in the dictionary; otherwise add
    the missing lines.

    If the file contains a "source" or "source-directory" directive, process
    the specified files recursively.
    """
    if fname in handled:
        return
    rdebug('Trying to add interface data to {fname}'.format(fname=fname))
    handled.add(fname)

    def is_new_stanza(s):
        """
        Check if a line starting with the `s` word actually starts a new
        stanza in an /etc/network/interfaces-like file.
        """
        if s in ('iface', 'mapping', 'auto', 'source', 'source-directory'):
            return True
        return s.startswith('allow-')

    basedir = os.path.dirname(fname)
    in_iface = ''
    left = []
    with open(fname, mode='r') as f:
        with tempfile.NamedTemporaryFile(dir=basedir,
                                         mode='w+t',
                                         delete=True) as tempf:
            updated = False

            while True:
                ln = f.readline()
                if not ln:
                    break
                stripped = ln.strip()
                words = stripped.split()
                if not words:
                    print(ln, file=tempf, end='')
                    continue

                if in_iface:
                    if is_new_stanza(words[0]):
                        for missing in left:
                            print(missing, file=tempf)
                            updated = True
                        in_iface = False
                    else:
                        left = list(filter(lambda s: s != stripped, left))

                print(ln, file=tempf, end='')

                # A separate conditional, since we may fall through.
                if not in_iface and len(words) > 1:
                    if words[0] == 'iface' and words[1] in data:
                        in_iface = words[1]
                        left = data[in_iface]
                    elif words[0] == 'source':
                        for new_fname in filter(lambda s: os.path.isfile(s),
                                                glob.glob(words[1])):
                            fixup_interfaces_file(new_fname, data, handled)
                    elif words[0] == 'source-directory':
                        for new_fname in filter(lambda s: os.path.isfile(s),
                                                glob.glob(words[1] + '/*')):
                            fixup_interfaces_file(new_fname, data, handled)

            if in_iface:
                for missing in left:
                    print(missing, file=tempf)
                    updated = True

            if updated:
                rdebug('Updating {fname}'.format(fname=fname))
                tempf.flush()
                txn.install(tempf.name, fname, exact=True)
            else:
                rdebug('No need to update {fname}'.format(fname=fname))

    rdebug('Done adding interface data to {fname}'.format(fname=fname))


def fixup_interfaces(ifaces):
    """
    Modify the system network configuration to add the post-up commands to
    the StorPool interfaces.
    """
    rdebug('fixup_interfaces invoked for {ifaces}'.format(ifaces=ifaces))

    # Parse the interface names
    data = {}
    for iface_data in ifaces.split(','):
        parts = iface_data.split('=', 1)
        iface = parts[0]
        if len(parts) == 2:
            mtu = parts[1]
        else:
            mtu = '9000'

        parts = iface.split('.', 1)
        if len(parts) == 2:
            vlan = True
            parent = parts[0]
        else:
            vlan = False
            parent = ''

        if vlan:
            subst = {
                'IFACE': iface,
                'MTU': mtu,
                'IF_VLAN_RAW_DEVICE': parent,
            }
            data[iface] = list(map(lambda s: s.format(**subst), vlandef))
            subst = {
                'IFACE': parent,
                'MTU': mtu,
            }
            data[parent] = list(map(lambda s: s.format(**subst), nonvlandef))
        else:
            subst = {
                'IFACE': iface,
                'MTU': mtu,
            }
            data[iface] = list(map(lambda s: s.format(**subst), nonvlandef))

    rdebug('Gone through the interfaces, got data: {data}'.format(data=data))

    rdebug('Now about to go through the system network configuration...')
    fixup_interfaces_file('/etc/network/interfaces', data, set())
