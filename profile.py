#!/usr/bin/env python3
"""Read-only target discovery and validated JSON profiles; never execute profiles."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

SCHEMA = 1
DISK = re.compile(r'/dev/(?:[sv]d[a-z]+|xvd[a-z]+|nvme[0-9]+n[0-9]+)')
FIELDS = {'schema', 'hostname', 'mac', 'ssh_port', 'root_device', 'root_uuid',
          'target_disk', 'disk_bytes', 'interface', 'ipv4', 'gateway', 'boot_mode',
          'root_fstype', 'authorized_keys_sha256'}
IDENTITY = ('schema', 'hostname', 'mac', 'ssh_port', 'authorized_keys_sha256')


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def canonical(profile):
    return json.dumps(profile, sort_keys=True, separators=(',', ':')) + '\n'


def fingerprint(profile):
    return hashlib.sha256(canonical({k: profile[k] for k in IDENTITY}).encode()).hexdigest()


def validate(profile):
    if not isinstance(profile, dict) or set(profile) != FIELDS:
        raise ValueError('Unsupported or incomplete target profile; collect it again')
    if type(profile['schema']) is not int or profile['schema'] != SCHEMA:
        raise ValueError('Unsupported profile schema')
    for key in FIELDS - {'schema', 'disk_bytes', 'ssh_port'}:
        if not isinstance(profile[key], str):
            raise ValueError(f'{key} must be a string')
    hostname = profile['hostname']
    if len(hostname) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',
                                                 label) for label in hostname.split('.')):
        raise ValueError('Invalid hostname')
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', profile['mac']):
        raise ValueError('Invalid MAC address')
    if profile['mac'] == '00:00:00:00:00:00' or int(profile['mac'][:2], 16) & 1:
        raise ValueError('Expected a unicast MAC address')
    if type(profile['ssh_port']) is not int or not 1 <= profile['ssh_port'] <= 65535:
        raise ValueError('Invalid SSH port')
    disk = profile['target_disk']
    if not DISK.fullmatch(disk):
        raise ValueError('Only direct virtio/SCSI/SATA/Xen/NVMe system disks are supported')
    suffix = 'p' if disk[-1].isdigit() else ''
    if not re.fullmatch(re.escape(disk + suffix) + r'[1-9][0-9]*', profile['root_device']):
        raise ValueError('Root must be a direct partition of the detected disk; LVM/RAID is unsupported')
    if not re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', profile['root_uuid']):
        raise ValueError('Invalid ext4 root UUID')
    if type(profile['disk_bytes']) is not int or profile['disk_bytes'] <= 0:
        raise ValueError('Invalid disk size')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', profile['interface']):
        raise ValueError('Invalid network interface name')
    if not isinstance(ipaddress.ip_interface(profile['ipv4']), ipaddress.IPv4Interface):
        raise ValueError('IPv4 is required')
    if not isinstance(ipaddress.ip_address(profile['gateway']), ipaddress.IPv4Address):
        raise ValueError('IPv4 gateway is required')
    if profile['boot_mode'] != 'BIOS' or profile['root_fstype'] != 'ext4':
        raise ValueError('This version supports BIOS with an ext4 root only')
    if not re.fullmatch(r'[0-9a-f]{64}', profile['authorized_keys_sha256']):
        raise ValueError('Invalid public-key file digest')
    return profile


def load(path):
    return validate(json.loads(Path(path).read_text()))


def collect(ssh_port=None):
    if os.geteuid() != 0:
        raise ValueError('Run discovery as root on the target')
    if command('uname', '-m') != 'x86_64' or Path('/sys/firmware/efi').is_dir():
        raise ValueError('This version requires x86_64 BIOS boot')
    if command('findmnt', '-n', '-o', 'TARGET', '--target', '/boot') != '/':
        raise ValueError('A separate /boot is not supported')
    root = str(Path(command('findmnt', '-n', '-o', 'SOURCE', '/')).resolve())
    if command('lsblk', '-ndo', 'TYPE', root) != 'part':
        raise ValueError('Root must be a direct disk partition; LVM/RAID/encryption is unsupported')
    parent = command('lsblk', '-ndo', 'PKNAME', root)
    if not parent or '\n' in parent or '/' in parent:
        raise ValueError('Cannot determine one parent system disk')
    disk = '/dev/' + parent
    if command('lsblk', '-ndo', 'TYPE', disk) != 'disk':
        raise ValueError('Root parent is not a direct disk')
    # Other data partitions would be lost, so refuse to infer permission to erase them.
    layout = json.loads(command('lsblk', '--json', '-o', 'PATH,TYPE,FSTYPE,PARTTYPE', disk))['blockdevices'][0]
    boot_types = {'21686148-6449-6e6f-744e-656564454649',
                  'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'}
    for partition in layout.get('children', []):
        if partition.get('children'):
            raise ValueError('Layered disk devices are unsupported')
        if partition['path'] != root:
            parttype = (partition.get('parttype') or '').lower()
            if parttype not in boot_types or partition.get('fstype') not in (None, '', 'vfat'):
                raise ValueError('Other data/unknown partitions exist on the system disk; review before DD')
    routes = json.loads(command('ip', '-j', '-4', 'route', 'show', 'default'))
    routes = [r for r in routes if r.get('dev') and r.get('gateway') and 'linkdown' not in r.get('flags', [])]
    if not routes:
        raise ValueError('No IPv4 default route with a gateway')
    metric = min(r.get('metric', 0) for r in routes)
    best = [r for r in routes if r.get('metric', 0) == metric]
    if len({(r['dev'], r['gateway']) for r in best}) != 1:
        raise ValueError('Ambiguous IPv4 default routes')
    route = best[0]
    interface = route['dev']
    # Reject VLANs, bridges and bonds: retaining their MAC alone cannot recreate them.
    if not Path(f'/sys/class/net/{interface}/device').exists():
        raise ValueError('Only a direct physical/virtual NIC is supported, without VLAN/bridge/bond')
    addresses = json.loads(command('ip', '-j', '-4', 'addr', 'show', 'dev', interface))[0]['addr_info']
    addresses = [a for a in addresses if a.get('scope') == 'global' and a.get('family') == 'inet']
    preferred = route.get('prefsrc')
    if preferred:
        addresses = [a for a in addresses if a['local'] == preferred]
    if len(addresses) != 1:
        raise ValueError('Cannot choose one global IPv4 address')
    address = addresses[0]
    if route.get('protocol') != 'dhcp' and not address.get('dynamic'):
        raise ValueError('DHCP IPv4 could not be confirmed; static-network migration is unsupported')
    ports = sorted({int(line.split()[1]) for line in command('sshd', '-T').splitlines()
                    if line.startswith('port ')})
    if ssh_port is None:
        connected = os.environ.get('SSH_CONNECTION', '').split()
        connected_port = int(connected[-1]) if len(connected) == 4 and connected[-1].isdigit() else None
        if connected_port in ports:
            ssh_port = connected_port
        elif len(ports) == 1:
            ssh_port = ports[0]
        else:
            raise ValueError('Multiple SSH ports; collect with --ssh-port PORT')
    if ssh_port not in ports:
        raise ValueError('Requested SSH port is not configured in sshd')
    keys = Path('/root/.ssh/authorized_keys').read_bytes()
    if not keys.strip():
        raise ValueError('The root authorized_keys file is empty')
    command('ssh-keygen', '-lf', '/root/.ssh/authorized_keys')
    return validate({
        'schema': SCHEMA, 'hostname': Path('/etc/hostname').read_text().strip(),
        'mac': Path(f'/sys/class/net/{interface}/address').read_text().strip().lower(),
        'ssh_port': ssh_port, 'root_device': root,
        'root_uuid': command('blkid', '-s', 'UUID', '-o', 'value', root),
        'target_disk': disk, 'disk_bytes': int(command('blockdev', '--getsize64', disk)),
        'interface': interface, 'ipv4': f"{address['local']}/{address['prefixlen']}",
        'gateway': route['gateway'], 'boot_mode': 'BIOS',
        'root_fstype': command('findmnt', '-n', '-o', 'FSTYPE', '/'),
        'authorized_keys_sha256': hashlib.sha256(keys).hexdigest(),
    })


def verify_current(expected, strict=False):
    current = collect(expected['ssh_port'])
    fields = tuple(expected) if strict else IDENTITY
    for field in fields:
        if current[field] != expected[field]:
            raise ValueError(f'Target changed: {field}. Collect/build for the current target again')
    return current


def shell(profile):
    names = {'hostname': 'P_HOSTNAME', 'mac': 'P_MAC', 'ssh_port': 'P_SSH_PORT',
             'root_device': 'P_ROOT_DEVICE', 'root_uuid': 'P_ROOT_UUID',
             'target_disk': 'P_TARGET_DISK', 'disk_bytes': 'P_DISK_BYTES',
             'interface': 'P_INTERFACE', 'ipv4': 'P_IPV4', 'gateway': 'P_GATEWAY',
             'authorized_keys_sha256': 'P_KEYS_SHA256'}
    variables = {name: str(profile[key]) for key, name in names.items()}
    disk = profile['target_disk']
    variables['P_NEW_ROOT'] = disk + ('p' if disk[-1].isdigit() else '') + '1'
    variables['PROFILE_ID'] = fingerprint(profile)
    return '\n'.join(f'export {name}={shlex.quote(value)}' for name, value in variables.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    discovery = sub.add_parser('collect')
    discovery.add_argument('output', nargs='?', default='/root/po0-target')
    discovery.add_argument('--ssh-port', type=int)
    for name in ('shell', 'validate', 'verify-current'):
        child = sub.add_parser(name)
        child.add_argument('profile')
        if name == 'verify-current':
            child.add_argument('--strict-boot', action='store_true')
    args = parser.parse_args()
    if args.action == 'collect':
        profile = collect(args.ssh_port)
        keys = Path('/root/.ssh/authorized_keys').read_bytes()
        if hashlib.sha256(keys).hexdigest() != profile['authorized_keys_sha256']:
            raise ValueError('Public keys changed during collection; try again')
        output = Path(args.output)
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output / 'target-profile.json').write_text(canonical(profile))
        (output / 'po0-authorized_keys').write_bytes(keys)
        for path in output.iterdir():
            path.chmod(0o600)
        print(f'Collected target configuration into {output}; keep it private')
    else:
        profile = load(args.profile)
        if args.action == 'shell':
            print(shell(profile))
        elif args.action == 'verify-current':
            print(canonical(verify_current(profile, args.strict_boot)), end='')
        else:
            print(fingerprint(profile))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, IndexError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
