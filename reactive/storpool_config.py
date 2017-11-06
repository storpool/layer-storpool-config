"""
A Juju layer for installing and configuring the base StorPool packages.
"""
from __future__ import print_function

import os
import tempfile
import subprocess

from charmhelpers.core import templating

from charms import reactive
from charmhelpers.core import hookenv

from spcharms import config as spconfig
from spcharms.confighelpers import network as spcnetwork
from spcharms import repo as sprepo
from spcharms import status as spstatus
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
    if spconf is None or spconf == '':
        rdebug('removing the config-available state')
        reactive.remove_state('l-storpool-config.config-available')
        reactive.remove_state('l-storpool-config.config-written')
        reactive.remove_state('l-storpool-config.config-network')
        reactive.remove_state('l-storpool-config.package-installed')
        reactive.remove_state('l-storpool-config.package-try-install')
        return

    rdebug('removing the config-written state')
    reactive.remove_state('l-storpool-config.config-written')

    rdebug('setting the config-available state')
    reactive.set_state('l-storpool-config.config-available')

    # And let's make sure we try installing any packages we need...
    reactive.remove_state('l-storpool-config.package-installed')
    reactive.set_state('l-storpool-config.package-try-install')

    # And the network configuration, too...
    reactive.remove_state('l-storpool-config.config-network')

    # This will probably race with some others, but oh well
    spstatus.npset('maintenance',
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
    spstatus.npset('maintenance',
                   'waiting for the StorPool charm configuration')


@reactive.when_not('storpool-repo-add.available')
@reactive.when('l-storpool-config.config-available')
@reactive.when_not('l-storpool-config.stopped')
def not_ready_no_repo():
    """
    Note that the `storpool-repo` layer has not yet completed its work.
    """
    rdebug('well, it seems we have a config, but we do not have a repo yet')
    spstatus.npset('maintenance', 'waiting for the StorPool repo setup')


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

    spstatus.npset('maintenance', 'obtaining the requested StorPool version')
    spver = hookenv.config().get('storpool_version', None)
    if spver is None or spver == '':
        rdebug('no storpool_version key in the charm config yet')
        return

    spstatus.npset('maintenance',
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
    spstatus.npset('maintenance', '')


@reactive.when('l-storpool-config.config-available',
               'l-storpool-config.package-installed')
@reactive.when_not('l-storpool-config.config-written')
@reactive.when_not('l-storpool-config.stopped')
def write_out_config():
    """
    Write out the StorPool configuration file specified in the charm config.
    """
    rdebug('about to write out the /etc/storpool.conf file')
    spstatus.npset('maintenance', 'updating the /etc/storpool.conf file')
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
    spstatus.npset('maintenance', '')


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

    rdebug('trying to parse the StorPool interface configuration')
    spstatus.npset('maintenance',
                   'parsing the StorPool interface configuration')
    cfg = spconfig.get_dict()
    ifaces = cfg.get('SP_IFACE', None)
    if ifaces is None:
        hookenv.set('error', 'No SP_IFACES in the StorPool config')
        return
    rdebug('got interfaces: {ifaces}'.format(ifaces=ifaces))

    spcnetwork.fixup_interfaces(ifaces)

    rdebug('well, looks like it is all done...')
    reactive.set_state('l-storpool-config.config-network')
    spstatus.npset('maintenance', '')


def reset_states():
    """
    Go through the whole install/configure cycle.
    """
    rdebug('state reset requested')
    reactive.remove_state('l-storpool-config.config-available')
    reactive.remove_state('l-storpool-config.package-try-install')
    reactive.remove_state('l-storpool-config.package-installed')
    reactive.remove_state('l-storpool-config.config-written')
    reactive.remove_state('l-storpool-config.config-network')


@reactive.hook('upgrade-charm')
def upgrade():
    """
    Go through the whole cycle on upgrade.
    """
    rdebug('upgrading the charm')
    reset_states()


@reactive.when('l-storpool-config.stop')
@reactive.when_not('l-storpool-config.stopped')
def remove_leftovers():
    """
    Clean up, remove configuration files, uninstall packages.
    """
    rdebug('storpool-config.stop invoked')
    reactive.remove_state('l-storpool-config.stop')
    reset_states()

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

    try:
        rdebug('about to roll back any txn-installed files')
        txn.rollback_if_needed()
    except Exception as e:
        rdebug('Could not run txn rollback: {e}'.format(e=e))

    if not sputils.check_in_lxc():
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
