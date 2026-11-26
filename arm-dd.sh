#!/bin/bash
# Schedule one RAM boot only; this script never reboots automatically.
set -Eeuo pipefail
umask 077
export LC_ALL=C
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd -P)
. "$SOURCE/common.sh"
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID == 0 ]] || die 'Run as root on Po0.'
cd /root/po0-official-dd
sha256sum -c SHA256SUMS
sha256sum -c BOOT_SHA256SUMS
load_profile BOOT_PROFILE.json
python3 ./profile.py verify-current BOOT_PROFILE.json --strict-boot >/dev/null
[[ $(cat PROFILE_ID) == "$PROFILE_ID" ]] || die 'Image/profile binding mismatch.'
uuid=$P_ROOT_UUID
[[ $(grub-probe --target=fs_uuid /boot) == "$uuid" ]] || die 'Unexpected GRUB boot filesystem.'
normal_id=gnulinux-simple-$uuid
[[ -e /boot/grub/grubenv ]] || die 'GRUB environment is missing.'
# Refuse an environment block with features GRUB cannot update during boot.
[[ $(findmnt -n -o FSTYPE --target /boot) == ext4 ]] || die 'Expected ext4 /boot.'
[[ ! -e /etc/grub.d/99_po0_official_dd && ! -e /etc/default/grub.d/99-po0-official-dd.cfg ]] || die 'DD menu already installed; inspect before arming it again.'
install -d -m 755 /etc/default/grub.d
cat > /etc/default/grub.d/99-po0-official-dd.cfg <<'EOF'
GRUB_DEFAULT=saved
GRUB_SAVEDEFAULT=false
GRUB_TIMEOUT=5
GRUB_TIMEOUT_STYLE=menu
EOF
cat > /etc/grub.d/99_po0_official_dd <<EOF
#!/bin/sh
cat <<'ENTRY'
menuentry 'Po0 official Debian offline DD' --id po0-official-dd {
    insmod gzio
    insmod part_gpt
    insmod part_msdos
    insmod ext2
    search --no-floppy --file --set=root /boot/po0-dd/vmlinuz
    linux /boot/po0-dd/vmlinuz console=ttyS0,115200n8 console=tty0 panic=30
    initrd /boot/po0-dd/initrd.img
}
ENTRY
EOF
chmod 755 /etc/grub.d/99_po0_official_dd
chmod 644 /etc/default/grub.d/99-po0-official-dd.cfg
update-grub
grub-script-check /boot/grub/grub.cfg
grep -Fq -- "$normal_id" /boot/grub/grub.cfg || die 'The normal Debian fallback menu entry is missing; do not reboot.'
grep -Fq -- 'menuentry '\''Po0 official Debian offline DD' /boot/grub/grub.cfg || die 'The RAM menu entry is missing; do not reboot.'
grub-set-default "$normal_id"
grub-reboot po0-official-dd
environment=$(grub-editenv /boot/grub/grubenv list)
[[ $environment == *"saved_entry=$normal_id"* && $environment == *'next_entry=po0-official-dd'* ]] || die 'The GRUB one-time entry was not recorded; do not reboot.'
printf '%s\n' "$environment"
printf '\nOne RAM boot is armed. The next reboot will overwrite ALL of %s.\n' "$P_TARGET_DISK"
printf 'Cancel before reboot: grub-editenv /boot/grub/grubenv unset next_entry\n'
printf 'RAM SSH uses the detected port %s and existing key.\n' "$P_SSH_PORT"
printf 'RAM progress: cat /run/po0-dd.status; tail /run/po0-dd.log\n'
printf 'After the final reboot, verify the NEW host fingerprint from SSH_HOST_ED25519.pub.\n'
printf 'No reboot has been performed by this script.\n'
