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
def not_ready_no_repo():
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

	if newly_installed:
		rdebug('it seems we managed to install some packages: {names}'.format(names=newly_installed))
		with open('/var/lib/storpool-config.packages', 'a') as f:
			print('\n'.join(newly_installed), file=f)
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
		subprocess.check_call(['env', 'TXN_INSTALL_MODULE=storpool-config', 'txn', 'install', '-c', '-o', 'root', '-g', 'root', '-m', '600', '--', spconf.name, '/etc/storpool.conf'])
		rdebug('it seems that /etc/storpool.conf has been created')

	rdebug('setting the config-written state')
	reactive.set_state('storpool-config.config-written')
	rdebug('removing the config-announced state')
	reactive.remove_state('storpool-config.config-announced')

@reactive.hook('stop')
def remove_leftovers():
	rdebug('storpool-config.stop invoked')
	reactive.remove_state('storpool-config.config-available')
	reactive.remove_state('storpool-config.package-try-install')
	reactive.remove_state('storpool-config.package-installed')
	reactive.remove_state('storpool-config.config-written')

	modules_b = subprocess.getoutput('txn list-modules')
	if modules_b is not None:
		rdebug('got some txn list-modules output')
		modules = modules_b.split('\n')
		rdebug('modules: {mod}'.format(mod=modules))
		have_config = 'storpool-config' in modules
		rdebug('have_config: {have}'.format(have=have_config))
		if have_config:
			rdebug('invoking txn rollback storpool-config')
			subprocess.call(['txn', 'rollback', 'storpool-config'])
	else:
		rdebug('looks like txn list-modules did not return anything meaningful')

	rdebug('let us see if we installed any packages')
	try:
		rdebug('about to open the packages file')
		with open('/var/lib/storpool-config.packages', 'r') as f:
			names = list(filter(lambda s: len(s) > 0, map(lambda d: d.rstrip(), f.readlines())))
			if names:
				rdebug('about to remove some packages: {names}'.format(names=names))
				cmd = ['apt-get', 'remove', '-y', '--'];
				cmd.extend(names)
				subprocess.call(cmd)
			else:
				rdebug('it looks like we did not install any packages')

		rdebug('about to remove the packages file')
		os.remove('/var/lib/storpool-config.packages', 'r')
	except Exception as e:
		rdebug('could not check for storpool-config.packages: {e}'.format(e=e))

	rdebug('goodbye, weird world!')
