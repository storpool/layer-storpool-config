import json
import os
import subprocess

from charmhelpers.core import hookenv

from spcharms import repo as sprepo

def module_name():
	return 'charm-' + hookenv.charm_name()

def install(*args, exact=False, prefix=''):
	cmd = ['env', 'TXN_INSTALL_MODULE=' + module_name(), 'txn', 'install-exact' if exact else 'install']
	cmd.extend(args)
	cmd[-1] = prefix + cmd[-1]
	subprocess.check_call(cmd)

def list_modules():
	modules = subprocess.getoutput('txn list-modules')
	if modules is None:
		return []
	else:
		return modules.split('\n')

def rollback_if_needed():
	if module_name() in list_modules():
		subprocess.call(['txn', 'rollback', module_name()])


class Txn(object):
	def __init__(self, prefix=''):
		self.prefix = prefix
	
	def install(self, *args, exact=False):
		install(*args, exact=exact, prefix=self.prefix)
	
	def install_exact(self, *args):
		self.install(*args, exact=True)

class LXD(object):
	@classmethod
	def list_all(klass):
		lxc_b = subprocess.check_output(['lxc', 'list', '--format=json'])
		lst = json.loads(lxc_b.decode())
		return map(lambda c: c['name'], lst)

	@classmethod
	def construct_all(klass):
		lst = [''] + list(klass.list_all())
		return map(lambda name: klass(name=name), lst)

	def __init__(self, name):
		self.name = name
		if name == '':
			self.prefix = ''
		else:
			self.prefix = '/var/lib/lxd/containers/{name}/rootfs'.format(name=name)
		self.txn = Txn(prefix=self.prefix)

	def exec_with_output(self, cmd):
		p = subprocess.Popen(['lxc', 'exec', self.name, '--'] + cmd, stdout=subprocess.PIPE)
		output = p.communicate()[0].decode()
		res = p.returncode
		return { 'res': res, 'out': output, }

	def copy_packages(self, *pkgnames):
		if self.prefix == '':
			return
		for pkgname in pkgnames:
			for f in sprepo.list_package_files(pkgname):
				if os.path.isfile(f):
					self.txn.install_exact(f, f)
				elif os.path.isdir(f):
					os.makedirs(self.prefix + f, mode=0o755, exist_ok=True)

	def get_package_tree(self, pkgname):
		if self.prefix == '':
			return []

		outside_b = subprocess.check_output(['dpkg-query', '-W', '-f', '${Version}', '--', pkgname])
		outside = outside_b.decode().split('\n')[0]

		present = self.exec_with_output(['dpkg-query', '-W', '-f', '${Version}', '--', pkgname])
		if present['res'] == 0:
			return []

		deps = list(map(
			lambda s: s[:-4] if s.endswith(':any') else s,
			map(
				lambda s: s.strip(' ').split(' ', 1)[0],
				subprocess.check_output(
					['dpkg-query', '-W', '-f', '${Depends}', '--', pkgname]
				).decode().split(',')
			)
		))
		res = [pkgname]
		for dep in deps:
			res.extend(self.get_package_tree(dep))
		return res

	def copy_package_trees(self, *pkgnames):
		for pkg in pkgnames:
			packages = self.get_package_tree(pkg)
			if packages:
				self.copy_packages(*packages)
