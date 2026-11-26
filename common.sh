#!/usr/bin/env bash
# Shared profile reader and disk/RAM configuration templates.
PO0_SOURCES=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

load_profile() {
    local assignments
    assignments=$(python3 "$PO0_SOURCES/profile.py" shell "$1") || return
    eval "$assignments"
}

write_system_configs() {
    local root=$1 mode=${2:-disk} pam=yes
    [[ $mode != ram ]] || pam=no
    install -d -m 755 "$root/etc/ssh" "$root/etc/systemd/network"
    cat > "$root/etc/systemd/network/10-po0.network" <<EOF
[Match]
MACAddress=$P_MAC
[Network]
DHCP=ipv4
IPv6AcceptRA=no
[DHCPv4]
ClientIdentifier=mac
EOF
    cat > "$root/etc/ssh/sshd_config" <<EOF
Port $P_SSH_PORT
AllowUsers root
PermitRootLogin prohibit-password
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
UsePAM $pam
X11Forwarding no
PrintMotd no
EOF
    if [[ $mode != ram ]]; then
        printf 'Subsystem sftp /usr/lib/openssh/sftp-server\n' >> "$root/etc/ssh/sshd_config"
        printf '%s\n' "$P_HOSTNAME" > "$root/etc/hostname"
        printf '127.0.0.1 localhost\n127.0.1.1 %s\n::1 localhost ip6-localhost ip6-loopback\n' \
            "$P_HOSTNAME" > "$root/etc/hosts"
        chmod 644 "$root/etc/hostname" "$root/etc/hosts"
    fi
    cat > "$root/etc/nftables.conf" <<EOF
#!/usr/sbin/nft -f
flush ruleset
table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;
        iifname "lo" accept
        ct state established,related accept
        ct state invalid drop
        meta l4proto { icmp, ipv6-icmp } accept
        udp sport 67 udp dport 68 accept
        tcp dport $P_SSH_PORT accept
    }
    chain forward {
        type filter hook forward priority filter; policy drop;
    }
    chain output {
        type filter hook output priority filter; policy accept;
    }
}
EOF
    chmod 644 "$root/etc/systemd/network/10-po0.network" "$root/etc/ssh/sshd_config" "$root/etc/nftables.conf"
}
