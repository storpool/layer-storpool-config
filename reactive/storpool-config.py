from __future__ import print_function

import pwd
import os
import time
import subprocess

from charmhelpers.core import templating

from charms import reactive
from charms.reactive import helpers as rhelpers
from charmhelpers.core import hookenv

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

@reactive.when('repo.available')
@reactive.when_not('storpool-config.config-available')
def not_ready_no_config(repo):
	rdebug('well, it seems we have a repo link, but we do not have a config yet')

@reactive.when_not('repo.available')
@reactive.when('storpool-config.config-available')
def not_ready_no_repo(repo):
	rdebug('well, it seems we have a config, but we do not have a repo link yet')

@reactive.when('repo.available', 'storpool-config.config-available', 'storpool-config.package-try-install')
@reactive.when_not('storpool-config.package-installed')
def install_package(repo):
	rdebug('the repo hook has become available and we do have the configuration')
	reactive.remove_state('storpool-config.package-try-install')
	(err, newly_installed) = repo.install_packages({
		'txn-install': '*',
		'storpool-config': '16.02.25.744ebef-1ubuntu1',
	})
	if err is not None:
		rdebug('oof, we could not install packages: {err}'.format(err=err))
		rdebug('removing the package-installed state')
		reactive.remove_state('storpool-config.package-installed')
		return

	rdebug('setting the package-installed state')
	reactive.set_state('storpool-config.package-installed')

@reactive.when('storpool-config.config-available', 'storpool-config.package-installed')
@reactive.when_not('storpool-config.config-written')
def write_out_config():
	rdebug('about to write out the /etc/storpool.conf file')
	rdebug('FIXME: do this to a temporary file and then run txn install on it')
	templating.render(source='storpool.conf',
	    target='/etc/storpool.conf',
	    owner='root',
	    perms=0o600,
	    context={
		'storpool_conf': hookenv.config()['storpool_conf'],
	    },
	)
	rdebug('setting the config-written state')
	reactive.set_state('storpool-config.config-written')
	rdebug('removing the config-announced state')
	reactive.remove_state('storpool-config.config-announced')

# FIXME: hook(stop): txn rollback storpool-config
