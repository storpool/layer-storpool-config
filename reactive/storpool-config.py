from __future__ import print_function

import pwd
import os
import tempfile
import time
import subprocess

from charmhelpers.core import templating

from charms import reactive
from charms.reactive import helpers as rhelpers
from charmhelpers.core import hookenv

from spcharms import config as spconfig
from spcharms.confighelpers import network as spcnetwork
from spcharms import repo as sprepo
from spcharms import txn

def rdebug(s):
	with open('/tmp/storpool-charms.log', 'a') as f:
		print('{tm} [config] {s}'.format(tm=time.ctime(), s=s), file=f)

@reactive.hook('config-changed')
def config_changed():
	rdebug('config-changed happened')
	config = hookenv.config()

	spconf = config.get('storpool_conf', None)
	rdebug('and we do{xnot} have a storpool_conf setting'.format(xnot=' not' if spconf is None else ''))
	if spconf is None:
		rdebug('removing the config-available state')
		reactive.remove_state('storpool-config.config-available')
		reactive.remove_state('storpool-config.config-written')
		reactive.remove_state('storpool-config.config-network')
		return

	if not config.changed('storpool_conf') and rhelpers.is_state('storpool-config.package-installed'):
		rdebug('apparently the storpool_conf setting has not changed')
		return

	rdebug('removing the config-written state')
	reactive.remove_state('storpool-config.config-written')

	rdebug('setting the config-available state')
	reactive.set_state('storpool-config.config-available')

	# And let's make sure we try installing any packages we need...
	reactive.remove_state('storpool-config.package-installed')
	reactive.set_state('storpool-config.package-try-install')

	# This will probably race with some others, but oh well
	hookenv.status_set('maintenance', 'waiting for the StorPool charm configuration and the StorPool repo setup')

@reactive.when('storpool-repo-add.available')
@reactive.when_not('storpool-config.config-available')
@reactive.when_not('storpool-config.stopping')
def not_ready_no_config():
	rdebug('well, it seems we have a repo, but we do not have a config yet')
	hookenv.status_set('maintenance', 'waiting for the StorPool charm configuration')

@reactive.when_not('storpool-repo-add.available')
@reactive.when('storpool-config.config-available')
@reactive.when_not('storpool-config.stopping')
def not_ready_no_repo():
	rdebug('well, it seems we have a config, but we do not have a repo yet')
	hookenv.status_set('maintenance', 'waiting for the StorPool repo setup')

@reactive.when('storpool-repo-add.available', 'storpool-config.config-available', 'storpool-config.package-try-install')
@reactive.when_not('storpool-config.package-installed')
@reactive.when_not('storpool-config.stopping')
def install_package():
	rdebug('the repo hook has become available and we do have the configuration')
	hookenv.status_set('maintenance', 'installing the StorPool configuration packages')
	reactive.remove_state('storpool-config.package-try-install')
	(err, newly_installed) = sprepo.install_packages({
		'txn-install': '*',
		'storpool-config': '16.02.25.744ebef-1ubuntu1',
	})
	if err is not None:
		rdebug('oof, we could not install packages: {err}'.format(err=err))
		rdebug('removing the package-installed state')
		reactive.remove_state('storpool-config.package-installed')
		return

	if newly_installed:
		rdebug('it seems we managed to install some packages: {names}'.format(names=newly_installed))
		sprepo.record_packages(newly_installed)
	else:
		rdebug('it seems that all the packages were installed already')

	rdebug('setting the package-installed state')
	reactive.set_state('storpool-config.package-installed')
	hookenv.status_set('maintenance', '')

@reactive.when('storpool-config.config-available', 'storpool-config.package-installed')
@reactive.when_not('storpool-config.config-written')
@reactive.when_not('storpool-config.stopping')
def write_out_config():
	rdebug('about to write out the /etc/storpool.conf file')
	hookenv.status_set('maintenance', 'updating the /etc/storpool.conf file')
	with tempfile.NamedTemporaryFile(dir='/tmp', mode='w+t', delete=True) as spconf:
		rdebug('about to write the contents to the temporary file {sp}'.format(sp=spconf.name))
		templating.render(source='storpool.conf',
		    target=spconf.name,
		    owner='root',
		    perms=0o600,
		    context={
			'storpool_conf': hookenv.config()['storpool_conf'],
		    },
		)
		rdebug('about to invoke txn install')
		txn.install('-o', 'root', '-g', 'root', '-m', '600', '--', spconf.name, '/etc/storpool.conf')
		rdebug('it seems that /etc/storpool.conf has been created')

		rdebug('trying to read it now')
		spconfig.drop_cache()
		cfg = spconfig.get_dict()
		rdebug('got {len} keys in the StorPool config'.format(len=len(cfg)))

	rdebug('setting the config-written state')
	reactive.set_state('storpool-config.config-written')
	hookenv.status_set('maintenance', '')

@reactive.when('storpool-config.config-written')
@reactive.when_not('storpool-config.config-network')
@reactive.when_not('storpool-config.stopping')
def setup_interfaces():
	ifdata = spcnetwork.read_interfaces(rdebug)
	if ifdata is None:
		return
	rdebug('lots of interface data: {ifaces}'.format(ifaces=ifdata['interfaces']))

	rdebug('trying to parse the StorPool interface configuration')
	hookenv.status_set('maintenance', 'parsing the StorPool interface configuration')
	cfg = spconfig.get_dict()
	ifaces = cfg.get('SP_IFACE', None)
	if ifaces is None:
		hookenv.set('error', 'No SP_IFACES in the StorPool config')
		return
	rdebug('got interfaces: {ifaces}'.format(ifaces=ifaces))
	for iface in ifaces.split(','):
		if len(iface) < 1:
			continue
		rdebug('trying for interface {iface}'.format(iface=iface))
		if iface not in ifdata['interfaces']:
			spcnetwork.add_interface(ifdata, iface, rdebug)
		else:
			spcnetwork.update_interface_if_needed(ifdata, iface, rdebug)
		rdebug('is it in now? {ifin}'.format(ifin=iface in ifdata['interfaces']))

	if ifdata['changed']:
		changed_interfaces = sorted(ifdata['changed-interfaces'])
		rdebug('trying to bring the changed interfaces down now')
		handled = set()
		for iface in reversed(changed_interfaces):
			if iface in handled:
				continue
			rdebug('trying to bring interface {iface} down'.format(iface=iface))
			subprocess.call(['ifdown', iface])
					
		with tempfile.NamedTemporaryFile(dir='/tmp', mode='w+t', delete=True) as spifaces:
			rdebug('about to write the new interfaces configuration to the temporary file {sp}'.format(sp=spifaces.name))
			spcnetwork.write_interfaces(ifdata, spifaces.name, rdebug)
			rdebug('about to invoke txn install')
			txn.install('-o', 'root', '-g', 'root', '-m', '644', '--', spifaces.name, '/etc/network/interfaces')
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
	reactive.set_state('storpool-config.config-network')
	hookenv.status_set('maintenance', '')

def reset_states():
	rdebug('state reset requested')
	reactive.remove_state('storpool-config.config-available')
	reactive.remove_state('storpool-config.package-try-install')
	reactive.remove_state('storpool-config.package-installed')
	reactive.remove_state('storpool-config.config-written')

@reactive.hook('stop')
def remove_leftovers():
	rdebug('storpool-config.stop invoked')
	reactive.set_state('storpool-config.stopping')
	reset_states()

	rdebug('about to roll back any txn-installed files')
	txn.rollback_if_needed()

	rdebug('about to uninstall any packages that we have installed')
	sprepo.uninstall_recorded_packages()

	rdebug('goodbye, weird world!')
