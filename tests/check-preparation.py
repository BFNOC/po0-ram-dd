"""Exercise resolver permissions and APT failure handling in a disposable container."""
from pathlib import Path
import os
import stat
import subprocess
import tempfile

if not Path('/.dockerenv').is_file():
    raise SystemExit('Run this check only inside a disposable container.')

source = Path('/work/prepare-image.sh').read_text()


def bash(script, **kwargs):
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True, **kwargs)


with tempfile.TemporaryDirectory() as directory:
    tmp = Path(directory)
    tmp.chmod(0o755)
    root = tmp / 'root'
    (root / 'etc').mkdir(parents=True, mode=0o755)
    resolver = root / 'etc/resolv.conf'
    environment = dict(os.environ, ROOT=str(root))

    # Reproduce the old copy's unreadable resolver, then execute the fixed block.
    old_copy = bash('umask 077; cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"', env=environment)
    assert old_copy.returncode == 0, old_copy.stderr
    assert stat.S_IMODE(resolver.stat().st_mode) == 0o600
    read_as_apt = ['runuser', '-u', '_apt', '--', 'cat', str(resolver)]
    assert subprocess.run(read_as_apt, capture_output=True).returncode != 0
    block = source.split('mount --bind /dev/pts "$ROOT/dev/pts"\n', 1)[1].split('\nlog ', 1)[0]
    fixed_copy = bash('set -e; umask 077\n' + block, env=environment)
    assert fixed_copy.returncode == 0, fixed_copy.stderr
    assert stat.S_IMODE(resolver.stat().st_mode) == 0o644
    read = subprocess.run(read_as_apt, capture_output=True, check=True)
    assert read.stdout == Path('/etc/resolv.conf').read_bytes()
    print('PASS: DNS configuration is readable by _apt even with umask 077')

    # A refused local connection reliably exercises APT's transient-error path.
    sources = tmp / 'sources.list'
    sources.write_text('deb [trusted=yes] http://127.0.0.1:1/debian trixie main\n')
    lists = tmp / 'lists'
    lists.mkdir(mode=0o755)
    apt_config = tmp / 'apt.conf'
    apt_config.write_text(f'''
Dir::Etc::sourcelist "{sources}";
Dir::Etc::sourceparts "-";
Dir::State::lists "{lists}/";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Acquire::Retries "0";
Acquire::http::Timeout "1";
''')
    environment['APT_CONFIG'] = str(apt_config)
    default = bash('apt-get update', env=environment)
    assert default.returncode == 0 and 'Failed to fetch' in default.stderr, default.stdout + default.stderr
    updates = [line for line in source.splitlines()
               if line.startswith(('apt-get ', 'chroot "$ROOT" ')) and line.endswith(' update')]
    assert len(updates) == 2, updates
    sentinel = tmp / 'continued-after-failure'
    for line in updates:
        checked = bash('set -e\napt_args=()\nchroot() { shift; "$@"; }\n' + line
                       + '\ntouch "' + str(sentinel) + '"\n', env=environment)
        assert checked.returncode != 0, checked.stdout + checked.stderr
        assert not sentinel.exists(), 'Package changes continued after an APT index failure'
    print('PASS: both APT update steps stop preparation when an index download fails')
