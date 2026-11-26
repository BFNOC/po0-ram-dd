FROM debian:trixie-slim AS configs
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o APT::Update::Error-Mode=any update && \
    apt-get install -y --no-install-recommends python3 openssh-server nftables ca-certificates shellcheck && \
    rm -rf /var/lib/apt/lists/*

FROM configs AS ram
RUN apt-get -o APT::Update::Error-Mode=any update && \
    apt-get install -y --no-install-recommends qemu-system-x86 linux-image-cloud-amd64 \
      initramfs-tools busybox xz-utils fdisk e2fsprogs kmod cpio iproute2 && \
    rm -rf /var/lib/apt/lists/*
