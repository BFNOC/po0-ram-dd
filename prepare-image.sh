#!/usr/bin/env bash
# Prepare a target-specific Debian 13 image on the relay.
set -Eeuo pipefail
umask 077
export LC_ALL=C
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd -P)
. "$SOURCE/common.sh"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

[[ $EUID == 0 ]] || die 'Run this script as root on the relay VPS.'
[[ $(uname -m) == x86_64 ]] || die 'The relay must be x86_64.'
. /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID:-} == 13 ]] || die 'This script is for the Debian 13 relay.'

BASE=/root/po0-official
IMAGE=$BASE/debian-13-generic-amd64.qcow2
PROFILE_DIR=${1:-$BASE/target}
PROFILE_DIR=$(readlink -f -- "$PROFILE_DIR")
KEYS=$PROFILE_DIR/po0-authorized_keys
PROFILE=$PROFILE_DIR/target-profile.json
load_profile "$PROFILE"
[[ -s $IMAGE && -s $BASE/SHA512SUMS ]] || die 'Missing the downloaded official image or SHA512SUMS.'
[[ -s $KEYS ]] || die 'Missing the public keys collected from the target.'
[[ $(sha256sum "$KEYS" | awk '{print $1}') == "$P_KEYS_SHA256" ]] || die 'Public keys do not match the target profile.'
KEYS=$(readlink -f -- "$KEYS")
[[ ! -e $BASE/prepared ]] || die 'The prepared directory already exists; inspect it before preparing another image.'

log 'Verify the original official image'
cd "$BASE"
awk '$2 == "debian-13-generic-amd64.qcow2" || $2 == "*debian-13-generic-amd64.qcow2" {print; found++} END {if (found != 1) exit 1}' \
    SHA512SUMS > .selected-SHA512SUMS
sha512sum -c .selected-SHA512SUMS
rm .selected-SHA512SUMS

log 'Install relay tools using Debian official repositories'
sources=$(mktemp /root/po0-tools-sources.XXXXXX)
printf '%s\n' \
    'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main' \
    'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://security.debian.org/debian-security trixie-security main' > "$sources"
apt_args=(-o "Dir::Etc::sourcelist=$sources" -o 'Dir::Etc::sourceparts=-' -o APT::Get::List-Cleanup=0)
apt-get "${apt_args[@]}" -o APT::Update::Error-Mode=any update
apt-get "${apt_args[@]}" install -y --no-install-recommends qemu-utils xz-utils util-linux e2fsprogs python3 openssh-client
rm "$sources"
ssh-keygen -lf "$KEYS" || die 'The public-key file is invalid.'

[[ $(df --output=avail -B1 "$BASE" | tail -1) -gt 4294967296 ]] || die 'At least 4 GiB free space is required.'
qemu-img check -f qcow2 "$IMAGE"
qemu-img info -f qcow2 --output=json "$IMAGE" | python3 -c '
import json, sys
i = json.load(sys.stdin)
assert not any(k in i for k in ("backing-filename", "full-backing-filename", "data-file")), "image depends on another file"
assert i["virtual-size"] <= 4 * 1024**3, "unexpected image size"
'

WORK=$(mktemp -d "$BASE/.prepare.XXXXXX")
ROOT=$WORK/root
LOOP=
cleanup() {
    local rc=$? failed=0
    trap - EXIT
    for dir in dev/pts dev sys proc ''; do
        if mountpoint -q "$ROOT${dir:+/$dir}"; then
            umount "$ROOT${dir:+/$dir}" || failed=1
        fi
    done
    if (( failed )); then
        printf 'Cleanup could not unmount everything. Retained work directory: %s\n' "$WORK" >&2
        exit 1
    fi
    if [[ -n $LOOP ]]; then
        losetup -d "$LOOP" || { printf 'Loop device still active: %s\n' "$LOOP" >&2; exit 1; }
    fi
    rm -rf -- "$WORK"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$ROOT"
log 'Convert the verified image to a sparse raw working copy'
qemu-img convert -p -f qcow2 -O raw "$IMAGE" "$WORK/debian13.raw"
LOOP=$(losetup --find --show --partscan "$WORK/debian13.raw")
udevadm settle 2>/dev/null || true
for _ in {1..20}; do [[ -b ${LOOP}p1 ]] && break; sleep 0.2; done
[[ $(blkid -s TYPE -o value "${LOOP}p1") == ext4 ]] || die 'Unexpected root partition; no image changes were made.'
mount "${LOOP}p1" "$ROOT"
[[ -f $ROOT/etc/debian_version ]] || die 'The mounted filesystem is not Debian.'
grep -qx 'VERSION_ID="13"\|VERSION_ID=13' "$ROOT/etc/os-release" || die 'The image is not Debian 13.'
[[ ! -e $ROOT/usr/sbin/policy-rc.d ]] || die 'Unexpected policy-rc.d in the original image.'

log 'Use official Debian package repositories in the new system'
rm -f "$ROOT/etc/apt/sources.list"
find "$ROOT/etc/apt/sources.list.d" -maxdepth 1 -type f \( -name '*.list' -o -name '*.sources' \) -delete
cat > "$ROOT/etc/apt/sources.list.d/debian.sources" <<'EOF'
Types: deb
URIs: https://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://security.debian.org/debian-security
Suites: trixie-security
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
chmod 644 "$ROOT/etc/apt/sources.list.d/debian.sources"

# Prevent package scripts from starting daemons inside the image.
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 755 "$ROOT/usr/sbin/policy-rc.d"
for dir in proc sys dev; do mount --bind "/$dir" "$ROOT/$dir"; done
mount --bind /dev/pts "$ROOT/dev/pts"
rm -f "$ROOT/etc/resolv.conf"
# APT downloads as _apt, which also needs to read DNS configuration.
install -m 644 /etc/resolv.conf "$ROOT/etc/resolv.conf"

log 'Remove cloud-init and cloud image helpers; install only the requested tools and system essentials'
chroot "$ROOT" env DEBIAN_FRONTEND=noninteractive apt-get -o APT::Update::Error-Mode=any update
# Optional packages can be absent from both the image and the package indexes.
purge_packages=()
for pkg in cloud-init cloud-initramfs-growroot cloud-utils cloud-image-utils cloud-guest-utils grub-cloud-amd64 \
    netplan.io netplan-generator python3-netplan qemu-guest-agent; do
    state=$(chroot "$ROOT" dpkg-query -W -f='${db:Status-Status}' "$pkg" 2>/dev/null || true)
    case "$state" in installed|config-files) purge_packages+=("$pkg") ;; esac
done
if (( ${#purge_packages[@]} )); then
    chroot "$ROOT" env DEBIAN_FRONTEND=noninteractive apt-get -y purge "${purge_packages[@]}"
fi
chroot "$ROOT" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    openssh-server systemd-resolved nftables vim curl wget unzip ca-certificates
# No autoremove: kernel, bootloader and networking dependencies must be retained.
chroot "$ROOT" apt-get clean
rm -f "$ROOT/usr/sbin/policy-rc.d"
rm -rf "$ROOT/etc/cloud" "$ROOT/var/lib/cloud" "$ROOT/var/log/cloud-init.log" "$ROOT/var/log/cloud-init-output.log"
rm -rf "$ROOT/var/lib/apt/lists/"*

log 'Configure the detected hostname, DHCP MAC, SSH port and nftables'
rm -f "$ROOT/etc/netplan/"*.yaml
write_system_configs "$ROOT"
systemctl --root="$ROOT" enable systemd-networkd.service systemd-resolved.service
rm -f "$ROOT/etc/resolv.conf"
ln -s /run/systemd/resolve/stub-resolv.conf "$ROOT/etc/resolv.conf"
install -d -m 700 "$ROOT/root/.ssh"
install -m 600 "$KEYS" "$ROOT/root/.ssh/authorized_keys"
# Unique to this target image; keep private and never clone to another machine.
rm -f "$ROOT/etc/ssh/ssh_host_"*
chroot "$ROOT" ssh-keygen -A
install -d -m 755 "$ROOT/run/sshd"
chroot "$ROOT" /usr/sbin/sshd -t
systemctl --root="$ROOT" enable ssh.service

systemctl --root="$ROOT" enable nftables.service

log 'Check the prepared system'
for pkg in cloud-init cloud-initramfs-growroot netplan.io netplan-generator qemu-guest-agent; do
    state=$(chroot "$ROOT" dpkg-query -W -f='${db:Status-Status}' "$pkg" 2>/dev/null || true)
    [[ $state != installed ]] || die "$pkg was not removed."
done
for path in usr/sbin/sshd usr/sbin/nft usr/lib/systemd/systemd-networkd usr/sbin/resize2fs; do
    [[ -x $ROOT/$path ]] || die "Missing required executable: $path"
done
compgen -G "$ROOT/boot/vmlinuz-*" >/dev/null || die 'The image has no kernel.'
compgen -G "$ROOT/boot/initrd.img-*" >/dev/null || die 'The image has no initramfs.'
chroot "$ROOT" /usr/sbin/sshd -T | awk '/^(port|permitrootlogin|passwordauthentication|pubkeyauthentication) /'
chroot "$ROOT" ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
cp "$ROOT/etc/ssh/ssh_host_ed25519_key.pub" "$WORK/SSH_HOST_ED25519.pub"
truncate -s 0 "$ROOT/etc/machine-id"
rm -f "$ROOT/var/lib/dbus/machine-id"
sync
for dir in dev/pts dev sys proc ''; do umount "$ROOT${dir:+/$dir}"; done
e2fsck -fn "${LOOP}p1"
losetup -d "$LOOP"
LOOP=

log 'Compress the prepared raw image (bounded decoder memory)'
mkdir "$WORK/out"
install -m 600 "$PROFILE" "$WORK/out/target-profile.json"
install -m 600 "$KEYS" "$WORK/out/po0-authorized_keys"
printf '%s\n' "$PROFILE_ID" > "$WORK/out/PROFILE_ID"
cp "$WORK/SSH_HOST_ED25519.pub" "$WORK/out/SSH_HOST_ED25519.pub"
stat -c %s "$WORK/debian13.raw" > "$WORK/out/RAW_BYTES"
sha256sum "$WORK/debian13.raw" | awk '{print $1}' > "$WORK/out/RAW_SHA256"
xz -T1 -3 -c "$WORK/debian13.raw" > "$WORK/out/debian13.raw.xz"
xz --memlimit-decompress=256MiB -t "$WORK/out/debian13.raw.xz"
(cd "$WORK/out" && sha256sum debian13.raw.xz RAW_BYTES RAW_SHA256 SSH_HOST_ED25519.pub PROFILE_ID target-profile.json po0-authorized_keys > SHA256SUMS)
mv "$WORK/out" "$BASE/prepared"
log 'Prepared image is ready; no Po0 boot or disk changes have been made'
du -h "$BASE/prepared/debian13.raw.xz"
printf 'Output: %s/prepared/\nKeep this directory private: it contains the image SSH host private keys.\n' "$BASE"
