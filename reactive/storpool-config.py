"""
A Juju layer for installing and configuring the base StorPool packages.
"""
from __future__ import print_function

import os
import tempfile
import subprocess

from charmhelpers.core import templating

from charms import reactive
from charms.reactive import helpers as rhelpers
from charmhelpers.core import hookenv

from spcharms import config as spconfig
from spcharms.confighelpers import network as spcnetwork
from spcharms import repo as sprepo
from spcharms import txn
from spcharms import utils as sputils


def rdebug(s):
    """
    Pass the diagnostic message string `s` to the central diagnostic logger.
    """
    sputils.rdebug(s, prefix='config')


@reactive.hook('config-changed')
def config_changed():
    """
    Check if the configuration is complete or has been changed.
    """
    rdebug('config-changed happened')
    config = hookenv.config()

    spconf = config.get('storpool_conf', None)
    rdebug('and we do{xnot} have a storpool_conf setting'
           .format(xnot=' not' if spconf is None else ''))
    if spconf is None:
        rdebug('removing the config-available state')
        reactive.remove_state('l-storpool-config.config-available')
        reactive.remove_state('l-storpool-config.config-written')
        reactive.remove_state('l-storpool-config.config-network')
        return

    if not config.changed('storpool_conf') and \
       rhelpers.is_state('l-storpool-config.package-installed'):
        rdebug('apparently the storpool_conf setting has not changed')
        return

    rdebug('removing the config-written state')
    reactive.remove_state('l-storpool-config.config-written')

    rdebug('setting the config-available state')
    reactive.set_state('l-storpool-config.config-available')

    # And let's make sure we try installing any packages we need...
    reactive.remove_state('l-storpool-config.package-installed')
    reactive.set_state('l-storpool-config.package-try-install')

    # This will probably race with some others, but oh well
    hookenv.status_set('maintenance',
                       'waiting for the StorPool charm configuration and '
                       'the StorPool repo setup')


@reactive.when('storpool-repo-add.available')
@reactive.when_not('l-storpool-config.config-available')
@reactive.when_not('l-storpool-config.stopped')
def not_ready_no_config():
    """
    Note that some configuration settings are missing.
    """
    rdebug('well, it seems we have a repo, but we do not have a config yet')
    hookenv.status_set('maintenance',
                       'waiting for the StorPool charm configuration')


@reactive.when_not('storpool-repo-add.available')
@reactive.when('l-storpool-config.config-available')
@reactive.when_not('l-storpool-config.stopped')
def not_ready_no_repo():
    """
    Note that the `storpool-repo` layer has not yet completed its work.
    """
    rdebug('well, it seems we have a config, but we do not have a repo yet')
    hookenv.status_set('maintenance', 'waiting for the StorPool repo setup')


@reactive.when('storpool-repo-add.available',
               'l-storpool-config.config-available',
               'l-storpool-config.package-try-install')
@reactive.when_not('l-storpool-config.package-installed')
@reactive.when_not('l-storpool-config.stopped')
def install_package():
    """
    Install the base StorPool packages.
    """
    rdebug('the repo hook has become available and '
           'we do have the configuration')

    hookenv.status_set('maintenance',
                       'obtaining the requested StorPool version')
    spver = hookenv.config().get('storpool_version', None)
    if spver is None or spver == '':
        rdebug('no storpool_version key in the charm config yet')
        return

    hookenv.status_set('maintenance',
                       'installing the StorPool configuration packages')
    reactive.remove_state('l-storpool-config.package-try-install')
    (err, newly_installed) = sprepo.install_packages({
        'txn-install': '*',
        'storpool-config': spver,
    })
    if err is not None:
        rdebug('oof, we could not install packages: {err}'.format(err=err))
        rdebug('removing the package-installed state')
        reactive.remove_state('l-storpool-config.package-installed')
        return

    if newly_installed:
        rdebug('it seems we managed to install some packages: {names}'
               .format(names=newly_installed))
        sprepo.record_packages('storpool-config', newly_installed)
    else:
        rdebug('it seems that all the packages were installed already')

    rdebug('setting the package-installed state')
    reactive.set_state('l-storpool-config.package-installed')
    hookenv.status_set('maintenance', '')


@reactive.when('l-storpool-config.config-available',
               'l-storpool-config.package-installed')
@reactive.when_not('l-storpool-config.config-written')
@reactive.when_not('l-storpool-config.stopped')
def write_out_config():
    """
    Write out the StorPool configuration file specified in the charm config.
    """
    rdebug('about to write out the /etc/storpool.conf file')
    hookenv.status_set('maintenance', 'updating the /etc/storpool.conf file')
    with tempfile.NamedTemporaryFile(dir='/tmp',
                                     mode='w+t',
                                     delete=True) as spconf:
        rdebug('about to write the contents to the temporary file {sp}'
               .format(sp=spconf.name))
        templating.render(source='storpool.conf',
                          target=spconf.name,
                          owner='root',
                          perms=0o600,
                          context={
                           'storpool_conf': hookenv.config()['storpool_conf'],
                          },
                          )
        rdebug('about to invoke txn install')
        txn.install('-o', 'root', '-g', 'root', '-m', '644', '--',
                    spconf.name, '/etc/storpool.conf')
        rdebug('it seems that /etc/storpool.conf has been created')

        rdebug('trying to read it now')
        spconfig.drop_cache()
        cfg = spconfig.get_dict()
        rdebug('got {len} keys in the StorPool config'.format(len=len(cfg)))

    rdebug('setting the config-written state')
    reactive.set_state('l-storpool-config.config-written')
    hookenv.status_set('maintenance', '')


def handle_interfaces():
    """
    Check whether any interfaces should be reconfigured.
    """
    cfg = spconfig.get_dict()
    return cfg.get('SP_IFACE_NETWORKS', '') != ''


@reactive.when('l-storpool-config.config-written')
@reactive.when_not('l-storpool-config.config-network')
@reactive.when_not('l-storpool-config.stopped')
def setup_interfaces():
    """
    Set up the IPv4 addresses of some interfaces if requested.
    """
    if sputils.check_in_lxc():
        rdebug('running in an LXC container, not setting up interfaces')
        reactive.set_state('l-storpool-config.config-network')
        return

    ifdata = spcnetwork.read_interfaces(rdebug)
    if ifdata is None:
        return
    rdebug('lots of interface data: {ifaces}'
           .format(ifaces=ifdata['interfaces']))

    rdebug('trying to parse the StorPool interface configuration')
    hookenv.status_set('maintenance',
                       'parsing the StorPool interface configuration')
    cfg = spconfig.get_dict()
    ifaces = cfg.get('SP_IFACE', None)
    if ifaces is None:
        hookenv.set('error', 'No SP_IFACES in the StorPool config')
        return
    rdebug('got interfaces: {ifaces}'.format(ifaces=ifaces))
    if not handle_interfaces():
        rdebug('no SP_IFACE_NETWORKS definition, not setting up or '
               'bringing any interfaces up or down')
        reactive.set_state('l-storpool-config.config-network')
        hookenv.status_set('maintenance', '')
        return

    for iface in ifaces.split(','):
        if len(iface) < 1:
            continue
        rdebug('trying for interface {iface}'.format(iface=iface))
        if iface not in ifdata['interfaces']:
            spcnetwork.add_interface(ifdata, iface, rdebug)
        else:
            spcnetwork.update_interface_if_needed(ifdata, iface, rdebug)
        rdebug('is it in now? {ifin}'
               .format(ifin=iface in ifdata['interfaces']))

    if ifdata['changed']:
        changed_interfaces = sorted(ifdata['changed-interfaces'])
        rdebug('trying to bring the changed interfaces down now')
        handled = set()
        for iface in reversed(changed_interfaces):
            if iface in handled:
                continue
            rdebug('trying to bring interface {iface} down'
                   .format(iface=iface))
            subprocess.call(['ifdown', iface])

        with tempfile.NamedTemporaryFile(dir='/tmp',
                                         mode='w+t',
                                         delete=True) as spifaces:
            rdebug('about to write the new interfaces configuration to '
                   'the temporary file {sp}'.format(sp=spifaces.name))
            spcnetwork.write_interfaces(ifdata, spifaces.name, rdebug)
            spifaces.flush()
            rdebug('about to invoke txn install')
            txn.install('-o', 'root', '-g', 'root', '-m', '644', '--',
                        spifaces.name, '/etc/network/interfaces')
            rdebug('it seems that /etc/network/interfaces has been updated')

        rdebug('trying to bring the changed interfaces up now')
        handled = set()
        for iface in changed_interfaces:
            if iface in handled:
                continue
            rdebug('trying to bring interface {iface} up'.format(iface=iface))
            subprocess.call(['ifup', iface])
    else:
        rdebug('no change, no need to update /etc/network/interfaces')

    rdebug('trying to bring the StorPool interfaces up now')
    handled = set()
    for iface in ifaces.split(','):
        if iface in handled:
            continue
        rdebug('trying to bring interface {iface} up'.format(iface=iface))
        subprocess.check_call(['ifup', iface])

    rdebug('well, looks like it is all done...')
    reactive.set_state('l-storpool-config.config-network')
    hookenv.status_set('maintenance', '')


def reset_states():
    """
    Go through the whole install/configure cycle.
    """
    rdebug('state reset requested')
    reactive.remove_state('l-storpool-config.config-available')
    reactive.remove_state('l-storpool-config.package-try-install')
    reactive.remove_state('l-storpool-config.package-installed')
    reactive.remove_state('l-storpool-config.config-written')


@reactive.when('l-storpool-config.stop')
@reactive.when_not('l-storpool-config.stopped')
def remove_leftovers():
    """
    Clean up, remove configuration files, uninstall packages.
    """
    rdebug('storpool-config.stop invoked')
    reactive.remove_state('l-storpool-config.stop')
    reset_states()

    try:
        do_handle_interfaces = handle_interfaces()
    except Exception:
        do_handle_interfaces = False

    if not sputils.check_in_lxc():
        try:
            rdebug('about to run "txn rollback" in all the containers')
            for lxd in txn.LXD.construct_all():
                if lxd.prefix == '':
                    continue
                if not os.path.exists(lxd.prefix + '/var/lib/txn/txn.index'):
                    rdebug('- no txn.index in the {name} container, skipping'
                           .format(name=lxd.name))
                    continue
                rdebug('- about to run "txn rollback" in {name}'
                       .format(name=lxd.name))
                res = lxd.exec_with_output(['txn', '--', 'rollback',
                                            txn.module_name()])
                rdebug('  - txn rollback completed: {res}'.format(res=res))
        except Exception as e:
            rdebug('Could not run "txn rollback" in all the containers: {e}'
                   .format(e=e))

        if do_handle_interfaces:
            try:
                rdebug('about to bring any interfaces that we were using down')
                cfg = spconfig.get_dict()
                ifaces = cfg['SP_IFACE'].split(',')
                for iface in ifaces:
                    rdebug('bringing {iface} down'.format(iface=iface))
                    subprocess.call(['ifdown', iface])
                    if iface.find('.') != -1:
                        parent = iface.split('.', 1)[0]
                        rdebug('also bringing {iface} down'
                               .format(iface=parent))
                        subprocess.call(['ifdown', parent])
            except Exception as e:
                rdebug('Could not bring the interfaces down: {e}'.format(e=e))

    try:
        rdebug('about to roll back any txn-installed files')
        txn.rollback_if_needed()
    except Exception as e:
        rdebug('Could not run txn rollback: {e}'.format(e=e))

    if not sputils.check_in_lxc():
        if do_handle_interfaces:
            try:
                rdebug('about to try to bring any interfaces that '
                       'we just brought down back up')
                for iface in ifaces:
                    if iface.find('.') != -1:
                        parent = iface.split('.', 1)[0]
                        rdebug('first bringing {iface} up'
                               .format(iface=parent))
                        subprocess.call(['ifup', parent])
                    rdebug('bringing {iface} up'.format(iface=iface))
                    subprocess.call(['ifup', iface])
            except Exception as e:
                rdebug('Could not bring the interfaces up: {e}'.format(e=e))

        try:
            rdebug('about to remove any loaded kernel modules')

            mods_b = subprocess.check_output(['lsmod'])
            for module_data in mods_b.decode().split('\n'):
                module = module_data.split(' ', 1)[0]
                rdebug('- got module {mod}'.format(mod=module))
                if module.startswith('storpool_'):
                    rdebug('  - trying to remove it')
                    subprocess.call(['rmmod', module])

            # Any remaining? (not an error, just, well...)
            rdebug('checking for any remaining StorPool modules')
            remaining = []
            mods_b = subprocess.check_output(['lsmod'])
            for module_data in mods_b.decode().split('\n'):
                module = module_data.split(' ', 1)[0]
                if module.startswith('storpool_'):
                    remaining.append(module)
            if remaining:
                rdebug('some modules were left over: {lst}'
                       .format(lst=' '.join(sorted(remaining))))
            else:
                rdebug('looks like we got rid of them all!')

            rdebug('that is all for the modules')
        except Exception as e:
            rdebug('Could not remove kernel modules: {e}'.format(e=e))

    rdebug('removing any config-related packages')
    sprepo.unrecord_packages('storpool-config')

    rdebug('let the storpool-repo layer know that we are shutting down')
    reactive.set_state('storpool-repo-add.stop')

    rdebug('goodbye, weird world!')
    reactive.set_state('l-storpool-config.stopped')
