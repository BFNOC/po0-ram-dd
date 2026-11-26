"""Read-only discovery, profile validation and same-target reuse regression tests."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('target_profile', Path(__file__).resolve().parents[1] / 'profile.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

KEYS = b'ssh-ed25519 AAAATESTONLY example\n'
PROFILE = dict(schema=1, hostname='example-node', mac='02:00:00:00:00:01', ssh_port=2222,
               root_device='/dev/vda1', root_uuid='b29c05cf-547d-4d37-a91a-186172794a27',
               target_disk='/dev/vda', disk_bytes=21474836480, interface='ens5',
               ipv4='192.0.2.10/24', gateway='192.0.2.1', boot_mode='BIOS', root_fstype='ext4',
               authorized_keys_sha256=hashlib.sha256(KEYS).hexdigest())


class DiscoveryFixture:
    def __init__(self, profile=None):
        self.profile = dict(profile or PROFILE)
        self.routes = [dict(dev=self.profile['interface'], gateway=self.profile['gateway'],
                            protocol='dhcp', prefsrc='192.0.2.10', metric=100)]
        self.addresses = [dict(local='192.0.2.10', prefixlen=24, family='inet', scope='global')]
        self.ports = [self.profile['ssh_port']]
        self.extra_partitions = []
        self.root_type = 'part'
        self.nic_device = True

    def command(self, *args):
        cfg = self.profile
        commands = {
            ('uname', '-m'): 'x86_64',
            ('findmnt', '-n', '-o', 'TARGET', '--target', '/boot'): '/',
            ('findmnt', '-n', '-o', 'SOURCE', '/'): cfg['root_device'],
            ('findmnt', '-n', '-o', 'FSTYPE', '/'): cfg['root_fstype'],
            ('lsblk', '-ndo', 'TYPE', cfg['root_device']): self.root_type,
            ('lsblk', '-ndo', 'PKNAME', cfg['root_device']): cfg['target_disk'][5:],
            ('lsblk', '-ndo', 'TYPE', cfg['target_disk']): 'disk',
            ('lsblk', '--json', '-o', 'PATH,TYPE,FSTYPE,PARTTYPE', cfg['target_disk']): json.dumps({
                'blockdevices': [dict(path=cfg['target_disk'], children=[dict(path=cfg['root_device'],
                                         type='part', fstype='ext4')] + self.extra_partitions)]}),
            ('ip', '-j', '-4', 'route', 'show', 'default'): json.dumps(self.routes),
            ('ip', '-j', '-4', 'addr', 'show', 'dev', cfg['interface']): json.dumps([dict(addr_info=self.addresses)]),
            ('sshd', '-T'): ''.join(f'port {port}\n' for port in self.ports),
            ('ssh-keygen', '-lf', '/root/.ssh/authorized_keys'): 'synthetic public key',
            ('blkid', '-s', 'UUID', '-o', 'value', cfg['root_device']): cfg['root_uuid'],
            ('blockdev', '--getsize64', cfg['target_disk']): str(cfg['disk_bytes']),
        }
        if args not in commands:
            raise AssertionError(f'Unexpected discovery command: {args}')
        return commands[args]

    @contextlib.contextmanager
    def active(self):
        read_text, read_bytes, exists, is_dir = Path.read_text, Path.read_bytes, Path.exists, Path.is_dir
        def text(path, *args, **kwargs):
            values = {'/etc/hostname': self.profile['hostname'] + '\n',
                      f"/sys/class/net/{self.profile['interface']}/address": self.profile['mac'] + '\n'}
            return values[str(path)] if str(path) in values else read_text(path, *args, **kwargs)
        def binary(path, *args, **kwargs):
            return KEYS if str(path) == '/root/.ssh/authorized_keys' else read_bytes(path, *args, **kwargs)
        def present(path):
            return self.nic_device if str(path).endswith('/device') else exists(path)
        def directory(path):
            return False if str(path) == '/sys/firmware/efi' else is_dir(path)
        with patch.object(p, 'command', self.command), patch.object(p.os, 'geteuid', return_value=0), \
             patch.object(Path, 'read_text', text), patch.object(Path, 'read_bytes', binary), \
             patch.object(Path, 'exists', present), patch.object(Path, 'is_dir', directory), \
             patch.dict(os.environ, {'SSH_CONNECTION': ''}):
            yield


class ProfileTests(unittest.TestCase):
    def test_detect_disks_and_old_root_partition(self):
        for disk, root, newroot in [('/dev/vda', '/dev/vda1', '/dev/vda1'),
                                   ('/dev/sda', '/dev/sda2', '/dev/sda1'),
                                   ('/dev/nvme0n1', '/dev/nvme0n1p3', '/dev/nvme0n1p1')]:
            fixture = DiscoveryFixture(dict(PROFILE, target_disk=disk, root_device=root))
            with fixture.active():
                result = p.collect()
            self.assertEqual(result, fixture.profile)
            self.assertIn('P_NEW_ROOT=' + newroot, p.shell(result))

    def test_reuse_same_identity_after_root_and_dhcp_change(self):
        changed = dict(PROFILE, root_uuid='af0a53de-4b40-4743-b6a3-f5f604c6344f',
                       root_device='/dev/sda2', target_disk='/dev/sda', disk_bytes=42949672960,
                       interface='ens7', ipv4='192.0.2.11/24')
        with patch.object(p, 'collect', return_value=changed):
            self.assertEqual(p.verify_current(PROFILE), changed)
            with self.assertRaisesRegex(ValueError, 'Target changed'):
                p.verify_current(PROFILE, strict=True)
        self.assertEqual(p.fingerprint(PROFILE), p.fingerprint(changed))

    def test_prevent_cloning_or_changed_login_settings(self):
        for field, value in [('mac', '02:00:00:00:00:02'), ('hostname', 'different-node'),
                             ('ssh_port', 31234), ('authorized_keys_sha256', 'f' * 64)]:
            changed = dict(PROFILE, **{field: value})
            self.assertNotEqual(p.fingerprint(PROFILE), p.fingerprint(changed))
            with patch.object(p, 'collect', return_value=changed), self.assertRaisesRegex(ValueError, field):
                p.verify_current(PROFILE)

    def test_unsafe_or_incomplete_profiles_rejected(self):
        for field, value in [('hostname', '$(touch /tmp/unwanted)'), ('mac', '01:00:00:00:00:01'),
                             ('ssh_port', True), ('disk_bytes', -1), ('interface', 'x;reboot'),
                             ('target_disk', '/dev/mapper/root'), ('root_device', '/dev/sdb1'),
                             ('ipv4', '::1/64'), ('gateway', '$(id)'), ('boot_mode', 'UEFI')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                p.validate(dict(PROFILE, **{field: value}))
        bad = dict(PROFILE, unknown='unexpected')
        with self.assertRaises(ValueError):
            p.validate(bad)

    def test_ambiguous_ssh_ports_require_choice(self):
        fixture = DiscoveryFixture()
        fixture.ports = [2222, 31234]
        with fixture.active():
            with self.assertRaisesRegex(ValueError, 'Multiple SSH ports'):
                p.collect()
            self.assertEqual(p.collect(31234)['ssh_port'], 31234)
            with patch.dict(os.environ, {'SSH_CONNECTION': '192.0.2.20 45678 192.0.2.10 2222'}):
                self.assertEqual(p.collect()['ssh_port'], 2222)

    def test_network_rejections(self):
        fixture = DiscoveryFixture()
        fixture.routes.append(dict(dev='ens7', gateway='192.0.2.2', protocol='dhcp', metric=100))
        with fixture.active(), self.assertRaisesRegex(ValueError, 'Ambiguous'):
            p.collect()
        fixture.routes = fixture.routes[:1]
        fixture.routes[0]['protocol'] = 'static'
        with fixture.active(), self.assertRaisesRegex(ValueError, 'DHCP'):
            p.collect()
        fixture.routes[0]['protocol'] = 'dhcp'
        fixture.nic_device = False
        with fixture.active(), self.assertRaisesRegex(ValueError, 'VLAN/bridge/bond'):
            p.collect()

    def test_other_data_partitions_and_lvm_rejected(self):
        fixture = DiscoveryFixture()
        for fstype in ('ext4', 'vfat', None):
            fixture.extra_partitions = [dict(path='/dev/vda2', fstype=fstype, parttype=None)]
            with fixture.active(), self.assertRaisesRegex(ValueError, 'Other data/unknown'):
                p.collect()
        fixture.extra_partitions = [dict(path='/dev/vda14', fstype=None,
                                         parttype='21686148-6449-6e6f-744e-656564454649'),
                                    dict(path='/dev/vda15', fstype='vfat',
                                         parttype='c12a7328-f81f-11d2-ba4b-00a0c93ec93b')]
        with fixture.active():
            self.assertEqual(p.collect(), PROFILE)
        fixture.root_type = 'lvm'
        with fixture.active(), self.assertRaisesRegex(ValueError, 'LVM/RAID'):
            p.collect()

    def test_collector_creates_only_private_profile_and_public_keys(self):
        fixture = DiscoveryFixture()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'target'
            with fixture.active(), patch.object(sys, 'argv', ['profile.py', 'collect', str(output)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                p.main()
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual({f.name for f in output.iterdir()}, {'target-profile.json', 'po0-authorized_keys'})
            for file in output.iterdir():
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            self.assertEqual(p.load(output / 'target-profile.json'), PROFILE)
            self.assertEqual((output / 'po0-authorized_keys').read_bytes(), KEYS)


if __name__ == '__main__':
    unittest.main()
