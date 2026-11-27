# 详细说明

[返回使用步骤](../README.md)

## 支持范围

- 目标机和中转机均为 Debian 13 x86_64；目标机使用 BIOS + GRUB。
- 根文件系统为系统盘上的直接 ext4 分区。支持 virtio、SCSI/SATA、Xen、NVMe 命名，新镜像的 root 为分区 1。
- 不支持单独 `/boot`、LVM、RAID、磁盘加密或同盘其他数据分区。原有 BIOS boot / EFI 分区可以通过准备检查，但 DD 仍会覆盖整个磁盘。
- 一张直接网卡提供明确的 DHCP IPv4 默认路由。不迁移静态网络、VLAN、bridge、bond 或额外网卡配置；默认路由有歧义时停止。
- 目标机约 2 GiB 或更多内存；暂存时至少 1.2 GiB 可用内存、1 GiB 可用磁盘空间。压缩镜像不超过 900 MiB，raw 不超过 4 GiB，系统盘不小于 raw。
- root 的 `authorized_keys` 不能为空，应只保留可信且持有对应私钥的公钥。
- 当前内核、`/lib/modules/`、initramfs-tools、GRUB、systemd-networkd、nftables 等工具须完整；暂存时缺少工具会停止，不联网补装。
- 中转机需要 Python 3，能访问 Debian 官方镜像和软件源，至少留出 4 GiB 磁盘空间。

## 镜像和参数

| 选项 | 官方镜像 | 内核更新跟踪包 |
| --- | --- | --- |
| `--variant genericcloud`（默认） | `debian-13-genericcloud-amd64.qcow2` | `linux-image-cloud-amd64` |
| `--variant generic` | `debian-13-generic-amd64.qcow2` | `linux-image-amd64` |

所选镜像与 `SHA512SUMS` 应来自[官方同一批次](https://cloud.debian.org/images/cloud/trixie/latest/)。脚本校验文件内容，没有另行验证 OpenPGP 签名；镜像缺失不会自动改用另一种。原始 qcow2 保持不变，在 raw 工作副本中定制后再压缩。

默认读取中转机 `/root/po0-official/target/`。其他采集目录可以作为位置参数传入：

```bash
bash prepare-image.sh --variant generic /root/my-target-profile
bash prepare-image.sh --help
```

配置了多个 SSH 端口时，采集脚本优先选择本次 SSH 连接的端口；无法判断时，指定一个已配置的端口：

```bash
bash collect-target.sh /root/po0-target --ssh-port 2222
```

采集结果目录、`prepared/` 以及 `/boot/po0-dd/` 已存在时，不直接覆盖。请先检查、归档原有文件，再操作。重复下载前也应确认目录中的镜像与校验文件是否匹配，避免 wget 另存为 `.1` 文件后误用旧文件。

## 新系统包含什么

使用 Debian 官方源，移除 cloud-init、cloud 辅助工具、netplan 和 qemu-guest-agent，不添加监控或业务服务。保留官方基础系统，并安装/保留所选内核、SSH、systemd-resolved、nftables、vim、curl、wget、unzip、ca-certificates；不执行 `autoremove`。

systemd-networkd 按采集到的 MAC 配置 DHCP。主机名、SSH 端口和 root 公钥来自采集配置；SSH 禁用密码认证，每份新镜像单独生成 SSH 主机密钥。

nftables 默认拒绝入站和转发，允许回环、已建立连接、ICMP、DHCP 响应和 SSH；出站允许。主机规则不另加来源 IP 白名单，云侧白名单和端口限制保持有效。

## 写盘过程和失败处理

`stage-ram-boot.sh` 构建、解包检查 RAM 环境；`arm-dd.sh` 再次校验并设置 GRUB 单次引导。两者均不重启，执行 `reboot` 才开始重装。

RAM 环境使用目标机当前内核，保留旧 SSH 主机密钥、登录公钥和端口。优先 DHCP，并复用 [reinstall-Po0](https://github.com/mihomoQ/reinstall-Po0) 的缺失地址恢复函数，见[上游说明](../UPSTREAM.md)。新系统的内核由制作镜像时的 `--variant` 决定。

镜像先复制到 RAM，再卸载旧根分区；完整校验 raw 后写盘，随后完整读回校验、离线扩容 GPT root/ext4，最后重启。不添加首次启动扩容服务。

重启前取消单次引导：

```bash
grub-editenv /boot/grub/grubenv unset next_entry
```

RAM 环境中查看进度：

```bash
cat /run/po0-dd.status
tail -n 50 /run/po0-dd.log
```

写盘前失败，会尝试把日志保存到旧系统 `/root/po0-dd-failure.log` 并重启回旧系统。写盘开始后失败，会保留 RAM 和临时 SSH，停止自动重启；此时不要重启。单次引导不能让已覆盖的旧系统恢复，无 VNC 时也不能保证所有引导或网络故障均可远程恢复。

重装后检查：

```bash
cat /etc/os-release
uname -r
df -h /
systemctl is-active ssh systemd-networkd systemd-resolved nftables
```

## 镜像复用

| 保留的文件 | 复用方式 |
| --- | --- |
| 官方原始 qcow2 和对应校验文件 | 可作为不同机器的制作起点；每台重新采集配置、定制镜像并生成主机密钥。 |
| 通用版完整 `prepared/` | 可用于同一台机器，要求 hostname、MAC、SSH 端口、公钥相同。UUID、盘符、容量和 DHCP 租约可以变化，暂存时重新读取。 |
| 早期专用版的 `debian13.raw.xz` | 留作原机私有备份，保留配套旧脚本；它没有通用版元数据，不能直接配上新版脚本使用。 |
| 只有 xz，没有配套元数据 | 不直接使用此流程，需要匹配的 raw 大小、校验和、主机指纹和配置记录。 |

成品镜像恢复的是制作时的软件、配置和密钥快照，不要直接克隆给不同机器。MAC、hostname、端口或公钥改变时重新制作；公网 IP 或 DHCP 租约变化本身不要求重做。

默认改为 `genericcloud` 仅影响新制作的镜像，已生成的制品保留原内核，格式不变。原来只下载了 `generic` 镜像的用户，应显式使用 `--variant generic`。

## 隐私和验证范围

采集目录包含机器信息和 root 公钥，成品镜像还包含 SSH 主机私钥；请私下传输和保存。Git 只记录源码，忽略规则不能清除已提交的历史，也不能防止强制添加文件。源码中的测试配置均为示例值。

DD 替换的是客体系统，不能移除云平台在宿主机、虚拟磁盘和网络层面的控制能力。

早期专用版已由用户在真实 Po0 验证：Debian 13 启动、公钥 SSH、整盘写入和读回校验、GPT/ext4 扩容及网络、nftables。后续通用配置和镜像选项版本没有再次在用户的真实机器上重装。

自动检查及未覆盖的情况见[测试说明](../tests/README.md)。
