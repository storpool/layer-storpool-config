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

@reactive.when('storpool-repo-add.available')
@reactive.when_not('storpool-config.config-available')
def not_ready_no_config():
	rdebug('well, it seems we have a repo, but we do not have a config yet')

@reactive.when_not('storpool-repo-add.available')
@reactive.when('storpool-config.config-available')
def not_ready_no_repo():
	rdebug('well, it seems we have a config, but we do not have a repo yet')

@reactive.when('storpool-repo-add.available', 'storpool-config.config-available', 'storpool-config.package-try-install')
@reactive.when_not('storpool-config.package-installed')
def install_package():
	rdebug('the repo hook has become available and we do have the configuration')
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

@reactive.when('storpool-config.config-available', 'storpool-config.package-installed')
@reactive.when_not('storpool-config.config-written')
def write_out_config():
	rdebug('about to write out the /etc/storpool.conf file')
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

	rdebug('setting the config-written state')
	reactive.set_state('storpool-config.config-written')

@reactive.hook('stop')
def remove_leftovers():
	rdebug('storpool-config.stop invoked')
	reactive.remove_state('storpool-config.config-available')
	reactive.remove_state('storpool-config.package-try-install')
	reactive.remove_state('storpool-config.package-installed')
	reactive.remove_state('storpool-config.config-written')

	rdebug('about to roll back any txn-installed files')
	txn.rollback_if_needed()

	rdebug('about to uninstall any packages that we have installed')
	sprepo.uninstall_recorded_packages()

	rdebug('goodbye, weird world!')
