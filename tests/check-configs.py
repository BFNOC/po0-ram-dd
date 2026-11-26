"""Render production templates and check real SSH/nftables in a disposable container."""
from pathlib import Path
import os
import re
import stat
import subprocess
import sys
import tempfile
import time

if not Path('/.dockerenv').is_file():
    raise SystemExit('Run only inside a disposable container; it changes container SSH files.')

sys.path.insert(0, '/work')
import profile as target_profile


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


with tempfile.TemporaryDirectory() as directory:
    tmp = Path(directory)
    Path('/run/sshd').mkdir(parents=True, exist_ok=True)
    run(['ssh-keygen', '-A'])
    client_key = tmp / 'client'
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(client_key)])
    Path('/root/.ssh').mkdir(mode=0o700, exist_ok=True)
    authorized = Path('/root/.ssh/authorized_keys')
    authorized.write_bytes(client_key.with_suffix('.pub').read_bytes())
    authorized.chmod(0o600)
    run(['usermod', '-L', 'root'])

    for port, mac, hostname in [(2222, '02:00:00:00:00:01', 'example-node'),
                                 (31234, '02:00:00:00:00:02', 'another-node.example')]:
        profile = dict(schema=1, hostname=hostname, mac=mac, ssh_port=port,
                       root_device='/dev/sda2', root_uuid='b29c05cf-547d-4d37-a91a-186172794a27',
                       target_disk='/dev/sda', disk_bytes=21474836480, interface='ens7',
                       ipv4='192.0.2.10/24', gateway='192.0.2.1', boot_mode='BIOS',
                       root_fstype='ext4', authorized_keys_sha256='0' * 64)
        target_profile.validate(profile)
        profile_file = tmp / 'profile.json'
        profile_file.write_text(target_profile.canonical(profile))
        for mode in ('disk', 'ram'):
            root = tmp / f'{port}-{mode}'
            root.mkdir()
            script = ('set -e; umask 077; . /work/common.sh; load_profile "$PROFILE"; '
                      'write_system_configs "$ROOT" "$MODE"')
            run(['bash', '-c', script], env=dict(os.environ, PROFILE=str(profile_file),
                                               ROOT=str(root), MODE=mode))
            ssh_config = root / 'etc/ssh/sshd_config'
            run(['/usr/sbin/sshd', '-t', '-f', str(ssh_config)])
            effective = run(['/usr/sbin/sshd', '-T', '-f', str(ssh_config)]).stdout
            for setting in [f'port {port}', 'passwordauthentication no', 'pubkeyauthentication yes',
                            'authorizedkeysfile .ssh/authorized_keys', 'allowusers root',
                            'usepam ' + ('yes' if mode == 'disk' else 'no')]:
                assert setting + '\n' in effective, setting
            if mode == 'disk':
                server = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(ssh_config)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                try:
                    for attempt in range(20):
                        result = subprocess.run(['ssh', '-p', str(port), '-i', str(client_key),
                                                 '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                                                 '-o', 'UserKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=2',
                                                 'root@127.0.0.1', 'true'], capture_output=True, text=True)
                        if result.returncode == 0:
                            break
                        if server.poll() is not None:
                            raise RuntimeError(server.stderr.read())
                        time.sleep(0.1)
                    else:
                        raise RuntimeError(result.stderr)
                finally:
                    server.terminate()
                    server.wait(timeout=5)
                assert (root / 'etc/hostname').read_text() == hostname + '\n'
                assert hostname in (root / 'etc/hosts').read_text()
            nft = root / 'etc/nftables.conf'
            assert f'tcp dport {port} accept' in nft.read_text()
            run(['nft', '-c', '-f', str(nft)])
            network = root / 'etc/systemd/network/10-po0.network'
            assert f'MACAddress={mac}\n' in network.read_text()
            assert 'DHCP=ipv4\n' in network.read_text() and 'ClientIdentifier=mac\n' in network.read_text()
            assert not re.search(r'^(Address|Gateway)=', network.read_text(), re.MULTILINE)
            for file in (network, ssh_config, nft):
                assert stat.S_IMODE(file.stat().st_mode) == 0o644, file
                assert stat.S_IMODE(file.parent.stat().st_mode) == 0o755, file.parent
        print(f'PASS: generated disk/RAM configs and real root key login on detected port {port}')
    source = Path('/work/prepare-image.sh').read_text()
    assert re.search(r'openssh-server systemd-resolved nftables vim curl wget unzip', source)
    print('PASS: DHCP, nftables, hostname, file modes and requested packages')
