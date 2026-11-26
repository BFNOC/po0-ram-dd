#!/bin/bash
# Run on Po0 after copying the complete prepared directory over SSH.
set -Eeuo pipefail
umask 077
export LC_ALL=C
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd -P)
. "$SOURCE/common.sh"
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID == 0 && $(uname -m) == x86_64 ]] || die 'Run as root on Po0 x86_64.'
[[ ! -d /sys/firmware/efi ]] || die 'Expected BIOS boot.'
DD_STAGE=$(cd -- "$(dirname -- "$0")" && pwd -P)
[[ $DD_STAGE == /root/po0-official-dd ]] || die 'Copy this directory to /root/po0-official-dd on Po0.'
cd "$DD_STAGE"
[[ ! -e /boot/po0-dd ]] || die '/boot/po0-dd already exists; inspect before building again.'
sha256sum -c SHA256SUMS
[[ -s target-profile.json && -s PROFILE_ID ]] || die 'Legacy image: prepare a new target-profile image before using these scripts.'
load_profile target-profile.json
[[ $(cat PROFILE_ID) == "$PROFILE_ID" ]] || die 'Image/profile binding mismatch.'
[[ $(sha256sum po0-authorized_keys | awk '{print $1}') == "$P_KEYS_SHA256" ]] || die 'Image public keys do not match its profile.'
[[ $(findmnt -n -o TARGET --target /boot) == / ]] || die 'A separate /boot needs a different GRUB path.'
[[ -s /root/.ssh/authorized_keys ]] || die 'No existing root SSH public keys.'
cmp /root/.ssh/authorized_keys po0-authorized_keys || die 'Image keys differ from current Po0 keys.'
for tool in mkinitramfs unmkinitramfs grub-reboot grub-set-default grub-probe grub-editenv \
    update-grub grub-script-check resize2fs e2fsck sfdisk blkid blockdev nft ip sshd bash; do
    command -v "$tool" >/dev/null || die "Missing $tool; do not reboot."
done
[[ -x /usr/lib/systemd/systemd-networkd && -x /usr/lib/systemd/systemd-udevd ]] || die 'Missing network or device daemon.'
[[ $(awk '/MemAvailable:/ {print $2}' /proc/meminfo) -gt 1200000 ]] || die 'Less than 1.2 GiB RAM available.'
[[ $(df --output=avail -B1 /boot | tail -1) -gt 1073741824 ]] || die 'Need 1 GiB free space for boot preparation.'
[[ $(cat RAW_BYTES) =~ ^[0-9]+$ && $(cat RAW_SHA256) =~ ^[0-9a-f]{64}$ ]] || die 'Invalid raw image metadata.'
[[ $(cat RAW_BYTES) -le 4294967296 ]] || die 'Unexpected raw image size.'
[[ $(stat -c %s debian13.raw.xz) -le 943718400 ]] || die 'Compressed image exceeds the 900 MiB RAM limit.'
chmod 755 xz
./xz --version
./xz --memlimit-decompress=256MiB -t debian13.raw.xz
# Re-read volatile state: the same target can have a new root UUID or DHCP lease.
python3 ./profile.py verify-current target-profile.json > BOOT_PROFILE.json.new
mv BOOT_PROFILE.json.new BOOT_PROFILE.json
load_profile BOOT_PROFILE.json
[[ $P_DISK_BYTES -ge $(cat RAW_BYTES) ]] || die 'Target disk is smaller than the raw image.'
DD_KVER=$(uname -r)
[[ -s /boot/vmlinuz-$DD_KVER && -d /lib/modules/$DD_KVER ]] || die 'Running kernel files are missing.'
mkdir -m 700 /boot/po0-dd
cat /proc/sys/kernel/random/uuid | tr -d '-' > BOOT_ID
export DD_STAGE DD_KVER
export DD_OUTPUT=/boot/po0-dd/initrd.img
bash ./build-ram-initrd.sh
install -m 600 "/boot/vmlinuz-$DD_KVER" /boot/po0-dd/vmlinuz
printf '==> Inspecting the generated initramfs\n'
VERIFY=$(mktemp -d "$DD_STAGE/.inspect.XXXXXX")
trap 'rm -rf -- "$VERIFY"' EXIT
unmkinitramfs "$DD_OUTPUT" "$VERIFY"
INITROOT=$VERIFY/main
[[ -f $INITROOT/init ]] || INITROOT=$VERIFY
cmp ram-init.sh "$INITROOT/init"
for path in usr/sbin/sshd usr/lib/openssh/sshd-session usr/lib/openssh/sshd-auth \
    usr/lib/systemd/systemd-networkd usr/lib/systemd/systemd-udevd usr/bin/xz; do
    [[ -x $INITROOT/$path ]] || die "Missing RAM executable: $path"
done
chroot "$INITROOT" /usr/bin/xz --version
chroot "$INITROOT" /bin/bash -n /init
install -d -m 755 "$INITROOT/run/sshd" "$INITROOT/dev"
[[ -e $INITROOT/dev/null ]] || mknod -m 666 "$INITROOT/dev/null" c 1 3
chroot "$INITROOT" /usr/sbin/sshd -t
sha256sum /boot/po0-dd/vmlinuz /boot/po0-dd/initrd.img BOOT_PROFILE.json BOOT_ID > BOOT_SHA256SUMS
printf '==> RAM files are ready. GRUB has not been changed; no reboot has been scheduled.\n'
ls -lh /boot/po0-dd/vmlinuz /boot/po0-dd/initrd.img
