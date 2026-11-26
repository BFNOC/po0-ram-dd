#!/bin/bash
# Run on the relay beside these source scripts, after prepare-image.sh succeeds.
set -Eeuo pipefail
umask 077
export LC_ALL=C
[[ $EUID == 0 ]] || { echo 'Run as root on the relay.' >&2; exit 1; }
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd -P)
PREPARED=/root/po0-official/prepared
cd "$PREPARED"
sha256sum -c SHA256SUMS
[[ -s target-profile.json && -s PROFILE_ID && -s po0-authorized_keys && -x /usr/bin/xz ]] || {
    echo 'Missing target-profile image metadata; do not mix this bundle with a legacy image.' >&2
    exit 1
}
. "$SOURCE/common.sh"
load_profile target-profile.json
[[ $(cat PROFILE_ID) == "$PROFILE_ID" ]]
[[ $(sha256sum po0-authorized_keys | awk '{print $1}') == "$P_KEYS_SHA256" ]]
for file in ram-init.sh build-ram-initrd.sh stage-ram-boot.sh arm-dd.sh po0-network-upstream.sh common.sh profile.py collect-target.sh LICENSE UPSTREAM.md; do
    install -m 700 "$SOURCE/$file" "$PREPARED/$file"
done
install -m 700 /usr/bin/xz "$PREPARED/xz"
sha256sum debian13.raw.xz RAW_BYTES RAW_SHA256 SSH_HOST_ED25519.pub \
    ram-init.sh build-ram-initrd.sh stage-ram-boot.sh arm-dd.sh xz po0-authorized_keys \
    target-profile.json PROFILE_ID common.sh profile.py collect-target.sh > SHA256SUMS.new
sha256sum po0-network-upstream.sh LICENSE UPSTREAM.md >> SHA256SUMS.new
mv SHA256SUMS.new SHA256SUMS
echo 'Copy the complete private prepared directory over SSH to Po0 as /root/po0-official-dd.'
echo 'Then, on Po0: bash /root/po0-official-dd/stage-ram-boot.sh'
echo 'That command builds and checks RAM files; it does not change GRUB or reboot.'
