import subprocess

from charmhelpers.core import hookenv

def module_name():
	return 'charm-' + hookenv.charm_name()

def install(*args):
	cmd = ['env', 'TXN_INSTALL_MODULE=' + module_name(), 'txn', 'install']
	cmd.extend(args)
	subprocess.check_call(cmd)

def list_modules():
	modules = subprocess.getoutput('txn list-modules')
	if modules is None:
		return []
	else:
		return modules.split('\n')

def rollback_if_needed():
	have_ours = module_name() in modules
	if module_name() in list_modules():
		subprocess.call(['txn', 'rollback', module_name()])
