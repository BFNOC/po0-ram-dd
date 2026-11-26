#!/bin/bash
# PID 1 for the temporary, offline Debian RAM environment.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077

mkdir -p /proc /sys /dev /run
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
ln -s /proc/self/fd /dev/fd
mkdir -p /dev/pts
mount -t devpts devpts /dev/pts
mount -t tmpfs -o mode=755,size=1024m tmpfs /run
mkdir -p /run/sshd /run/udev /mnt/old /root
chmod 755 /run/sshd /run/udev /dev/pts
exec > >(tee -a /run/po0-dd.log /dev/console) 2>&1

. /etc/po0-dd.conf
PO0_NETWORK_LIBRARY_ONLY=1
# shellcheck source=po0-network-upstream.sh
. /etc/po0-network-upstream.sh
WRITING=0
failure() {
    local rc=$?
    trap - ERR
    set +e
    printf '\nFAILED (exit %s); writing_started=%s\n' "$rc" "$WRITING"
    printf '%s\n' FAILED > /run/po0-dd.status
    [[ ! -f /run/po0-network.log ]] || cat /run/po0-network.log
    if (( WRITING == 0 )); then
        if [[ $(blkid -s UUID -o value "$OLD_ROOT" 2>/dev/null) == "$OLD_ROOT_UUID" ]]; then
            if mount -o remount,rw /mnt/old 2>/dev/null || mount -t ext4 -o rw "$OLD_ROOT" /mnt/old; then
                cp /run/po0-dd.log /mnt/old/root/po0-dd-failure.log
                umount /mnt/old
            fi
        fi
        echo 'The disk has not been overwritten. Returning to the old system in 30 seconds.'
        sync
        sleep 30
        reboot -f
    fi
    echo 'Disk replacement is incomplete. Keeping RAM SSH available; do not reboot.'
    while :; do sleep 3600; done
}
trap failure ERR

echo '==> Starting the offline RAM environment'
printf '%s\n' STARTING > /run/po0-dd.status
for module in virtio_pci virtio_blk virtio_net ext4; do modprobe "$module"; done
/usr/lib/systemd/systemd-udevd --daemon --resolve-names=never
udevadm trigger --action=add
udevadm settle --timeout=30

[[ -b "$TARGET_DISK" && -b "$OLD_ROOT" ]]
[[ $(blockdev --getsize64 "$TARGET_DISK") == "$DISK_BYTES" ]]
[[ $(blkid -s UUID -o value "$OLD_ROOT") == "$OLD_ROOT_UUID" ]]
[[ $(cat /sys/class/net/*/address) == *"$MAC"* ]]
[[ $(awk '/MemTotal:/ {print $2}' /proc/meminfo) -ge 1700000 ]]

echo "==> Starting temporary DHCP and key-only SSH on port $SSH_PORT"
mac_addr=$MAC
ipv4_addr=$OLD_IPV4
ipv4_gateway=$OLD_GATEWAY
ipv6_addr=
ipv6_gateway=
ethx=$(get_ethx)
ip link set lo up
mkdir -p /run/systemd/netif
chown systemd-network:systemd-network /run/systemd/netif
chmod 755 /run/systemd /run/systemd/netif
/usr/lib/systemd/systemd-networkd > >(tee /run/po0-network.log) 2>&1 &
for _ in {1..60}; do
    [[ -n $(get_first_ipv4_addr) && -n $(get_ipv4_gateway) ]] && break
    sleep 1
done
# Reuse reinstall-Po0's rescue logic: DHCP wins; fill only missing old state.
restore_missing_addresses || false
[[ -n $(get_first_ipv4_addr) && -n $(get_ipv4_gateway) ]]
nft -f /etc/nftables.conf
/usr/sbin/sshd -t
/usr/sbin/sshd -e -f /etc/ssh/sshd_config
echo 'RAM SSH is ready. Progress: cat /run/po0-dd.status; tail /run/po0-dd.log'

echo '==> Loading the compressed image into RAM'
printf '%s\n' LOADING > /run/po0-dd.status
mount -t ext4 -o ro,noload "$OLD_ROOT" /mnt/old
[[ $(cat "/mnt/old$IMAGE_DIR/BOOT_ID") == "$BOOT_ID" ]]
cp "/mnt/old$IMAGE_DIR/debian13.raw.xz" /run/debian13.raw.xz
umount /mnt/old
printf '%s  /run/debian13.raw.xz\n' "$XZ_SHA256" | sha256sum -c -
[[ $(blockdev --getsize64 "$TARGET_DISK") -ge "$RAW_BYTES" ]]

echo '==> Verifying the entire decompressed image before overwriting the disk'
printf '%s\n' VERIFYING > /run/po0-dd.status
xz --memlimit-decompress=256MiB -dc /run/debian13.raw.xz | sha256sum > /run/raw.sha256
read -r actual _ < /run/raw.sha256
[[ $actual == "$RAW_SHA256" ]]
[[ $(awk '$1 ~ /^\/dev\// {print}' /proc/mounts) == '' ]]

echo "==> Writing $TARGET_DISK from RAM; the old system will now be overwritten"
printf '%s\n' WRITING > /run/po0-dd.status
WRITING=1
xz --memlimit-decompress=256MiB -dc /run/debian13.raw.xz |
    dd of="$TARGET_DISK" bs=4M conv=fsync status=progress
sync

echo '==> Verifying the written disk before resizing'
printf '%s\n' READBACK > /run/po0-dd.status
dd if="$TARGET_DISK" bs=4M count="$RAW_BYTES" iflag=count_bytes status=none | sha256sum > /run/readback.sha256
read -r actual _ < /run/readback.sha256
[[ $actual == "$RAW_SHA256" ]]
udevadm settle --timeout=30
blockdev --rereadpt "$TARGET_DISK"
udevadm settle --timeout=30
[[ $(blkid -p -s TYPE -o value "$NEW_ROOT") == ext4 ]]

echo '==> Expanding partition 1 and its ext4 filesystem while offline'
printf '%s\n' RESIZING > /run/po0-dd.status
# sfdisk preserves other partitions and automatically repairs a relocated GPT backup.
printf ', +\n' | sfdisk --lock --no-reread -N 1 "$TARGET_DISK"
blockdev --rereadpt "$TARGET_DISK"
udevadm settle --timeout=30
fsck_rc=0
e2fsck -pf "$NEW_ROOT" || fsck_rc=$?
(( fsck_rc <= 1 ))
resize2fs "$NEW_ROOT"
sync

printf '%s\n' COMPLETE > /run/po0-dd.status
echo '==> Disk checksum and filesystem resize passed. Booting the new Debian in 10 seconds.'
sleep 10
reboot -f
while :; do sleep 3600; done
