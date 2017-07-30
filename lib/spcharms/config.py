import subprocess

cached_config = None

def get_cached_dict():
	global cached_config
	if cached_config is not None:
		return cached_config

	res = {}
	lines_b = subprocess.check_output(['/usr/sbin/storpool_confshow'])
	for line in lines_b.decode().split('\n'):
		fields = line.split('=', 1)
		if len(fields) < 2:
			continue
		res[fields[0]] = fields[1]
	cached_config = res
	return cached_config

def get_dict():
	return get_cached_dict()

def drop_cache():
	global cached_config
	cached_config = None
