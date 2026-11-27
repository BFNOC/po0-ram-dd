# po0-ram-dd

给外网受限的 Po0 重装 Debian 13：中转机准备官方镜像，Po0 从内存离线写盘。

默认 **cloud 内核**，移除 cloud-init；保留主机名、SSH 端口和登录公钥，装好 nftables、vim、curl、wget、unzip。

需要一台能正常联网的 Debian 13 x86_64 中转机，空闲磁盘至少 4 GiB。Po0 同样为 Debian 13 x86_64，使用 BIOS、ext4、DHCP，内存至少 2 GiB，已有 root 公钥登录。以下操作均使用 root。

## 1. Po0：导出配置

把 `collect-target.sh` 和 `profile.py` 放到 Po0 的 `/root/po0-tools/`，执行：

```bash
bash /root/po0-tools/collect-target.sh
```

把生成的 `/root/po0-target/` 通过 SSH/SFTP 复制到中转机，目录名改为 `/root/po0-official/target/`。

## 2. 中转机：制作镜像

把本仓库文件放到 `/root/po0-official/ram-scripts/`，然后执行：

```bash
cd /root/po0-official
wget https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
wget https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS
bash ram-scripts/prepare-image.sh && bash ram-scripts/bundle-for-po0.sh
```

完成后，把整个 `/root/po0-official/prepared/` 通过 SSH/SFTP 复制到 Po0，目标目录为 `/root/po0-official-dd/`，不要再套一层 `prepared/`。

**想用普通内核？** 下载 `debian-13-generic-amd64.qcow2`，并把上面的准备命令改为：

```bash
bash ram-scripts/prepare-image.sh --variant generic && bash ram-scripts/bundle-for-po0.sh
```

## 3. Po0：开始重装

**以下命令会在检查通过后重启，并覆盖整个系统盘。先备份需要的数据。**

```bash
cd /root/po0-official-dd
bash stage-ram-boot.sh && bash arm-dd.sh && reboot
```

写盘、校验和扩容会自动完成，随后重启进入新系统，用原端口和公钥登录。SSH 主机指纹会变化，请与中转机 `prepared/SSH_HOST_ED25519.pub` 核对。

镜像含 SSH 主机私钥，采集配置和 `prepared/` 目录请私下保存，不要公开上传。

[详细限制、故障处理与镜像复用](docs/details.md) · [测试说明](tests/README.md) · [上游参考](UPSTREAM.md)
