"""Exercise the real preparation CLI and checksum selection without mounting images."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

if not Path('/.dockerenv').is_file():
    raise SystemExit('Run only in a disposable container; this creates /root/po0-official.')

BASE = Path('/root/po0-official')
if BASE.exists():
    raise SystemExit('Test requires an empty disposable container; preparation directory already exists.')

SOURCE = Path('/work/prepare-image.sh')
keys = b'ssh-ed25519 AAAATESTONLY example\n'
profile = dict(schema=1, hostname='example-node', mac='02:00:00:00:00:01', ssh_port=2222,
               root_device='/dev/vda1', root_uuid='b29c05cf-547d-4d37-a91a-186172794a27',
               target_disk='/dev/vda', disk_bytes=21474836480, interface='ens5',
               ipv4='192.0.2.10/24', gateway='192.0.2.1', boot_mode='BIOS', root_fstype='ext4',
               authorized_keys_sha256=hashlib.sha256(keys).hexdigest())


with tempfile.TemporaryDirectory(prefix='po0-variant-tests-') as directory:
    tmp = Path(directory)
    commands = tmp / 'bin'
    commands.mkdir()
    apt_marker = tmp / 'apt-attempted'
    # Stop exactly at the first APT command, after CLI/profile/SHA512 validation.
    # No package changes, image conversion, loop setup, mounts or GRUB actions run.
    apt = commands / 'apt-get'
    apt.write_text('#!/bin/sh\ntouch "$APT_MARKER"\nexit 77\n')
    apt.chmod(0o755)
    environment = dict(os.environ, PATH=f'{commands}:' + os.environ['PATH'], APT_MARKER=str(apt_marker))

    def prepare(*args):
        apt_marker.unlink(missing_ok=True)
        result = subprocess.run(['bash', str(SOURCE), *args], env=environment, cwd=tmp,
                                capture_output=True, text=True)
        return result, apt_marker.exists()

    def reached_apt(args, variant):
        result, attempted = prepare(*args)
        assert result.returncode == 77 and attempted, result.stdout + result.stderr
        name = f'debian-13-{variant}-amd64.qcow2'
        package = 'linux-image-cloud-amd64' if variant == 'genericcloud' else 'linux-image-amd64'
        assert f'Selected image: {name}; kernel track: {package}' in result.stdout
        assert f'{name}: OK' in result.stdout, result.stdout

    result, attempted = prepare('--help')
    assert result.returncode == 0 and not attempted
    assert 'cloud-amd64 kernel (default)' in result.stdout
    assert not BASE.exists(), 'Help created a preparation directory'

    BASE.mkdir(mode=0o700)
    try:
        for target in (BASE / 'target', tmp / 'custom target'):
            target.mkdir(mode=0o700)
            (target / 'target-profile.json').write_text(json.dumps(profile))
            (target / 'po0-authorized_keys').write_bytes(keys)
        images = {v: BASE / f'debian-13-{v}-amd64.qcow2' for v in ('genericcloud', 'generic')}
        for variant, image in images.items():
            image.write_bytes(f'SYNTHETIC {variant} CHECKSUM FIXTURE ONLY\n'.encode())

        def sums(star=False):
            return ''.join(hashlib.sha512(image.read_bytes()).hexdigest() + ' '
                           + ('*' if star else ' ') + image.name + '\n' for image in images.values())

        checksum_file = BASE / 'SHA512SUMS'
        checksum_file.write_text(sums())
        for args, variant in [((), 'genericcloud'), (('--variant', 'genericcloud'), 'genericcloud'),
                              (('--variant', 'generic'), 'generic'), (('--variant=generic',), 'generic'),
                              (('custom target',), 'genericcloud'),
                              (('--variant', 'generic', 'custom target'), 'generic'),
                              (('custom target', '--variant', 'generic'), 'generic'),
                              (('--variant', 'generic', '--', 'custom target'), 'generic')]:
            reached_apt(args, variant)
        checksum_file.write_text(sums(star=True))
        reached_apt((), 'genericcloud')
        print('PASS: cloud default, both explicit variants, help, and old positional profile-directory syntax')

        for args in [('--variant',), ('--variant=',), ('--variant', 'unknown'),
                     ('--variant', 'generic', '--variant', 'genericcloud'),
                     ('--unknown',), ('custom target', 'extra'), ('--', 'custom target', 'extra')]:
            result, attempted = prepare(*args)
            assert result.returncode != 0 and not attempted, (args, result.stdout, result.stderr)
        checksum_file.write_text(sums())
        original_cloud = images['genericcloud'].read_bytes()
        images['genericcloud'].unlink()
        result, attempted = prepare()
        assert result.returncode != 0 and not attempted
        assert 'Missing ' + str(images['genericcloud']) in result.stderr
        reached_apt(('--variant', 'generic'), 'generic')
        images['genericcloud'].write_bytes(original_cloud)
        print('PASS: invalid arguments fail early; missing default image never silently selects generic')

        # Alter the unselected image: its digest must not block the selected variant.
        original_generic = images['generic'].read_bytes()
        images['generic'].write_bytes(b'changed unselected image\n')
        reached_apt((), 'genericcloud')
        result, attempted = prepare('--variant', 'generic')
        assert result.returncode != 0 and not attempted and 'FAILED' in result.stdout
        images['generic'].write_bytes(original_generic)
        checksum_file.write_text(sums().splitlines()[1] + '\n')
        result, attempted = prepare()
        assert result.returncode != 0 and not attempted
        checksum_file.write_text(sums() + sums().splitlines()[0] + '\n')
        result, attempted = prepare()
        assert result.returncode != 0 and not attempted
        print('PASS: selected-image SHA512 verification; wrong, missing or duplicate checksum entries stop before APT')

        # Execute the production kernel validation against disposable boot files.
        source = SOURCE.read_text()
        marker = '[[ $(chroot "$ROOT" dpkg-query -W '
        validation = marker + source.split(marker, 1)[1].split('\nchroot "$ROOT" /usr/sbin/sshd -T', 1)[0]
        root = tmp / 'image'
        boot = root / 'boot'
        boot.mkdir(parents=True)
        wrapper = 'set -eu; die() { echo "$*" >&2; exit 1; }; chroot() { printf "%s" "$PACKAGE_STATE"; };\n'

        def check_kernel(variant, kernels, initrd=True, package_state='installed'):
            for file in boot.iterdir():
                file.unlink()
            for version in kernels:
                (boot / f'vmlinuz-{version}').write_text('synthetic kernel\n')
                if initrd:
                    (boot / f'initrd.img-{version}').write_text('synthetic initramfs\n')
            package = 'linux-image-cloud-amd64' if variant == 'genericcloud' else 'linux-image-amd64'
            return subprocess.run(['bash', '-c', wrapper + validation], capture_output=True, text=True,
                                  env=dict(os.environ, ROOT=str(root), IMAGE_VARIANT=variant,
                                           KERNEL_PACKAGE=package, PACKAGE_STATE=package_state))

        cloud, generic = '6.12.118+deb13-cloud-amd64', '6.12.118+deb13-amd64'
        for variant, version in [('genericcloud', cloud), ('generic', generic)]:
            assert check_kernel(variant, [version]).returncode == 0
            for versions, initrd, state in [([version], False, 'installed'),
                                           ([version], True, 'not-installed'),
                                           ([cloud, generic], True, 'installed'),
                                           ([], True, 'installed')]:
                assert check_kernel(variant, versions, initrd, state).returncode != 0
        assert check_kernel('genericcloud', [generic]).returncode != 0
        assert check_kernel('generic', [cloud]).returncode != 0
        print('PASS: kernel flavor, matching initramfs and installed update-tracking metapackage checks')
        assert not (BASE / 'prepared').exists()
        assert not list(BASE.glob('.prepare.*'))
    finally:
        shutil.rmtree(BASE)
