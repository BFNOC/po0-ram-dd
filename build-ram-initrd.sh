#!/bin/bash
# Build only: does not change GRUB, arm a reboot or write to a block device.
set -Eeuo pipefail
umask 077
export LC_ALL=C
: "${DD_STAGE:?}" "${DD_KVER:?}" "${DD_OUTPUT:?}"
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd -P)
. "$SOURCE/common.sh"
load_profile "$DD_STAGE/BOOT_PROFILE.json"
DD_STAGE=$(readlink -f "$DD_STAGE")
export DD_STAGE DD_KVER
WORK=$(mktemp -d "$DD_STAGE/.ram-build.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
mkdir -p "$WORK/conf/hooks" "$WORK/conf/scripts" "$WORK/conf/conf.d"
printf 'MODULES=most\nBUSYBOX=y\nCOMPRESS=gzip\nRESUME=none\n' > "$WORK/conf/initramfs.conf"
touch "$WORK/conf/modules"
cat > "$WORK/conf/hooks/zz-po0-dd" <<'HOOK'
#!/bin/bash
set -eu
case "${1:-}" in prereqs) exit 0 ;; esac
. /usr/share/initramfs-tools/hook-functions
for tool in bash dd sha256sum stat cp cat awk grep tee sleep sync mkdir chown chmod mount umount ip \
    blkid blockdev sfdisk e2fsck resize2fs nft; do
    path=$(command -v "$tool")
    rm -f "$DESTDIR$path"
    copy_exec "$path"
done
rm -f "$DESTDIR/usr/bin/xz"
copy_exec "$DD_STAGE/xz" /usr/bin/xz
copy_exec /usr/sbin/sshd
for helper in /usr/lib/openssh/sshd-*; do copy_exec "$helper"; done
copy_exec /usr/lib/systemd/systemd-networkd
copy_exec /usr/lib/systemd/systemd-udevd
copy_exec "$(command -v udevadm)"
for module in virtio_pci virtio_blk virtio_scsi sd_mod nvme ahci virtio_net ext4 nf_tables nf_conntrack nft_ct; do
    manual_add_modules "$module"
done

mkdir -p "$DESTDIR/etc/ssh" "$DESTDIR/root/.ssh" "$DESTDIR/etc/systemd/network"
chmod 700 "$DESTDIR/root" "$DESTDIR/root/.ssh"
cp /etc/passwd /etc/group /etc/machine-id /etc/protocols "$DESTDIR/etc/"
# Only in RAM: allow a root account with key authentication and UsePAM=no.
# Password authentication and empty-password login remain disabled in sshd.
printf 'root::19000:0:99999:7:::\n' > "$DESTDIR/etc/shadow"
chmod 600 "$DESTDIR/etc/shadow"
cp /root/.ssh/authorized_keys "$DESTDIR/root/.ssh/authorized_keys"
chmod 600 "$DESTDIR/root/.ssh/authorized_keys"
cp /etc/ssh/ssh_host_* "$DESTDIR/etc/ssh/"
chmod 600 "$DESTDIR/etc/ssh/ssh_host_"*_key
printf 'passwd: files\ngroup: files\nshadow: files\nhosts: files dns\n' > "$DESTDIR/etc/nsswitch.conf"
printf '127.0.0.1 localhost\n' > "$DESTDIR/etc/hosts"
. "$DD_STAGE/common.sh"
write_system_configs "$DESTDIR" ram
cp "$DD_STAGE/ram-init.sh" "$DESTDIR/init"
cp "$DD_STAGE/po0-network-upstream.sh" "$DESTDIR/etc/po0-network-upstream.sh"
chmod 755 "$DESTDIR/init"
{
    printf 'OLD_ROOT_UUID=%s\nDISK_BYTES=%s\n' "$P_ROOT_UUID" "$P_DISK_BYTES"
    printf 'IMAGE_DIR=%s\n' "$DD_STAGE"
    printf 'BOOT_ID=%s\n' "$(cat "$DD_STAGE/BOOT_ID")"
    printf 'RAW_BYTES=%s\n' "$(cat "$DD_STAGE/RAW_BYTES")"
    printf 'RAW_SHA256=%s\n' "$(cat "$DD_STAGE/RAW_SHA256")"
    printf 'XZ_SHA256=%s\n' "$(sha256sum "$DD_STAGE/debian13.raw.xz" | cut -d ' ' -f1)"
    printf 'MAC=%s\nSSH_PORT=%s\n' "$P_MAC" "$P_SSH_PORT"
    printf 'TARGET_DISK=%s\nOLD_ROOT=%s\nNEW_ROOT=%s\n' "$P_TARGET_DISK" "$P_ROOT_DEVICE" "$P_NEW_ROOT"
    printf 'OLD_IPV4=%s\nOLD_GATEWAY=%s\n' "$P_IPV4" "$P_GATEWAY"
} > "$DESTDIR/etc/po0-dd.conf"
HOOK
chmod 755 "$WORK/conf/hooks/zz-po0-dd"
mkinitramfs -d "$WORK/conf" -o "$DD_OUTPUT" "$DD_KVER"
