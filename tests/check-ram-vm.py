"""Boot the real RAM init in QEMU and replace disposable image files only."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

if not Path('/.dockerenv').is_file():
    raise SystemExit('Run only in the disposable po0-dd-ram-validation container.')

bad_hash = '--bad-raw-hash' in sys.argv
scsi = '--scsi' in sys.argv
disk_device = '/dev/sda' if scsi else '/dev/vda'
old_partition = 2 if scsi else 1


def run(args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(' '.join(args[:2]) + '\n' + result.stdout + result.stderr)
    return result


with tempfile.TemporaryDirectory(prefix='po0-ram-vm-') as directory:
    tmp = Path(directory)
    stage = tmp / 'stage'
    stage.mkdir()
    rootfiles = tmp / 'old-files'
    rootfiles.mkdir()
    (rootfiles / 'root').mkdir()
    root_stage = rootfiles / str(stage).lstrip('/')
    root_stage.mkdir(parents=True)
    old_disk = tmp / 'old.raw'
    old_bytes = 1024 * 1024**2
    old_disk.touch()
    os.truncate(old_disk, old_bytes)
    run(['sfdisk', str(old_disk)], input=f'label: dos\n{old_partition} : start=2048, type=83\n')
    old_uuid = 'b29c05cf-547d-4d37-a91a-186172794a27'
    # A GPT cloud-image-like layout: BIOS partition, ESP, then root partition 1.
    raw = tmp / 'new.raw'
    raw.touch()
    os.truncate(raw, 128 * 1024**2)
    run(['sfdisk', str(raw)], input='label: gpt\n'
        '1 : start=16384, size=243679, type=L\n'
        '14 : start=2048, size=2048, type=21686148-6449-6E6F-744E-656564454649\n'
        '15 : start=4096, size=12288, type=U\n')
    newfiles = tmp / 'new-files'
    newfiles.mkdir()
    (newfiles / 'OFFICIAL_IMAGE_PROOF').write_text('new filesystem\n')
    run(['mkfs.ext4', '-q', '-F', '-U', 'af0a53de-4b40-4743-b6a3-f5f604c6344f',
         '-b', '4096', '-E', 'offset=8388608', '-d', str(newfiles), str(raw), '30459'])
    compressed = stage / 'debian13.raw.xz'
    with compressed.open('wb') as output:
        subprocess.run(['xz', '-T1', '-3', '-c', str(raw)], stdout=output, check=True)
    (stage / 'RAW_BYTES').write_text(str(raw.stat().st_size) + '\n')
    raw_hash = '0' * 64 if bad_hash else hashlib.sha256(raw.read_bytes()).hexdigest()
    (stage / 'RAW_SHA256').write_text(raw_hash + '\n')
    (stage / 'BOOT_ID').write_text('0123456789abcdef0123456789abcdef\n')
    shutil.copy('/usr/bin/xz', stage / 'xz')
    shutil.copy('/work/ram-init.sh', stage / 'ram-init.sh')
    shutil.copy('/work/po0-network-upstream.sh', stage / 'po0-network-upstream.sh')
    client_key = tmp / 'client'
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(client_key)])
    Path('/root/.ssh').mkdir(mode=0o700, exist_ok=True)
    shutil.copy(client_key.with_suffix('.pub'), '/root/.ssh/authorized_keys')
    run(['ssh-keygen', '-A'])
    for name in ('common.sh', 'profile.py'):
        shutil.copy('/work/' + name, stage / name)
    profile = dict(schema=1, hostname='vm-target', mac='02:00:00:00:00:01', ssh_port=2222,
                   root_device=f'{disk_device}{old_partition}', root_uuid=old_uuid,
                   target_disk=disk_device, disk_bytes=old_bytes, interface='ens3',
                   ipv4='10.0.2.15/24', gateway='10.0.2.2', boot_mode='BIOS', root_fstype='ext4',
                   authorized_keys_sha256=hashlib.sha256(client_key.with_suffix('.pub').read_bytes()).hexdigest())
    (stage / 'BOOT_PROFILE.json').write_text(json.dumps(profile))
    for file in stage.iterdir():
        shutil.copy(file, root_stage / file.name)
    run(['mkfs.ext4', '-q', '-F', '-U', old_uuid, '-b', '4096', '-E', 'offset=1048576',
         '-d', str(rootfiles), str(old_disk), str((old_bytes - 1048576) // 4096)])
    kernel = next(Path('/boot').glob('vmlinuz-*'))
    initrd = tmp / 'initrd.img'
    environment = dict(os.environ, DD_STAGE=str(stage), DD_KVER=kernel.name[len('vmlinuz-'):],
                       DD_OUTPUT=str(initrd))
    built = run(['bash', '/work/build-ram-initrd.sh'], env=environment)
    print('PASS: mkinitramfs built the real RAM environment', flush=True)
    print(built.stderr[-1200:], flush=True)
    inspection = Path('/work/stage-ram-boot.sh').read_text().split('VERIFY=$(mktemp ', 1)[1]
    inspection = 'VERIFY=$(mktemp ' + inspection.split('\nsha256sum /boot/', 1)[0]
    run(['bash', '-c', 'set -e\ndie() { echo "$*" >&2; exit 1; }\n' + inspection],
        env=environment, cwd=stage)
    print('PASS: generated initramfs inspection, chroot binary and SSH config checks', flush=True)
    serial = tmp / 'serial.log'
    qemu_args = ['qemu-system-x86_64', '-machine', 'accel=kvm:tcg', '-m', '2048', '-smp', '2',
                 '-kernel', str(kernel), '-initrd', str(initrd), '-append', 'console=ttyS0,115200n8 panic=5',
                 '-device', 'virtio-net-pci,netdev=net0,mac=02:00:00:00:00:01',
                 '-netdev', 'user,id=net0,hostfwd=tcp:127.0.0.1:10221-:2222',
                 '-display', 'none', '-serial', f'file:{serial}', '-monitor', 'none', '-no-reboot']
    if scsi:
        qemu_args += ['-device', 'virtio-scsi-pci,id=scsi',
                      '-drive', f'file={old_disk},format=raw,if=none,id=disk',
                      '-device', 'scsi-hd,drive=disk,bus=scsi.0']
    else:
        qemu_args += ['-drive', f'file={old_disk},format=raw,if=virtio']
    vm = subprocess.Popen(qemu_args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    ssh_ok = False
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if vm.poll() is not None:
                break
            log = serial.read_text(errors='replace') if serial.exists() else ''
            if ('FAILED (exit' in log and not bad_hash) or 'Kernel panic' in log:
                raise RuntimeError(log[-12000:])
            ssh = subprocess.run(['ssh', '-p', '10221', '-i', str(client_key),
                                  '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                                  '-o', 'UserKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=1',
                                  'root@127.0.0.1', 'cat /run/po0-dd.status'], capture_output=True, text=True)
            if ssh.returncode == 0:
                ssh_ok = True
            time.sleep(0.2)
        else:
            raise RuntimeError('QEMU timed out')
        log = serial.read_text(errors='replace')
        assert vm.returncode == 0, vm.stderr.read() + '\n' + log[-12000:]
        assert ssh_ok, 'RAM SSH login failed\n' + log[-12000:]
        if bad_hash:
            assert 'writing_started=0' in log and 'The disk has not been overwritten.' in log, log[-12000:]
            assert f'==> Writing {disk_device}' not in log, log[-12000:]
            table = json.loads(run(['sfdisk', '--json', str(old_disk)]).stdout)['partitiontable']
            assert table['label'] == 'dos', table
            old_root = tmp / 'old-root.raw'
            with old_disk.open('rb') as image, old_root.open('wb') as output:
                image.seek(1048576)
                shutil.copyfileobj(image, output)
            saved_log = run(['debugfs', '-R', 'cat /root/po0-dd-failure.log', str(old_root)]).stdout
            assert 'writing_started=0' in saved_log, saved_log
            print('PASS: bad image hash stops before DD, preserves old partitions, saves a failure log and reboots', flush=True)
            sys.exit(0)
        assert 'Disk checksum and filesystem resize passed.' in log, log[-16000:]
        table = json.loads(run(['sfdisk', '--json', str(old_disk)]).stdout)['partitiontable']
        parts = table['partitions']
        assert table['label'] == 'gpt' and len(parts) == 3, table
        rootpart = next(p for p in parts if p['node'].endswith('1'))
        assert rootpart['start'] == 16384 and rootpart['size'] > 1500000, rootpart
        resized_root = tmp / 'resized-root.raw'
        with old_disk.open('rb') as image, resized_root.open('wb') as output:
            image.seek(rootpart['start'] * 512)
            shutil.copyfileobj(image, output)
        checked = subprocess.run(['e2fsck', '-fn', str(resized_root)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        proof = run(['debugfs', '-R', 'cat /OFFICIAL_IMAGE_PROOF', str(resized_root)]).stdout
        assert proof == 'new filesystem\n', proof
        print(f'PASS: QEMU DHCP and root SSH, old root {disk_device}{old_partition}, new root {disk_device}1', flush=True)
        print('PASS: RAM image checksum, complete DD, disk readback checksum, GPT and ext4 expansion', flush=True)
    finally:
        if vm.poll() is None:
            vm.terminate()
            vm.wait(timeout=10)
