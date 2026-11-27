# Po0 官方 Debian 13 离线 DD

在可联网的 Debian 13 中转机上，校验并定制 Debian 官方 cloud image，移除 cloud-init 和云镜像辅助工具；目标机把镜像放入 RAM 后，离线写盘、校验和扩容。

本版本先从目标机导出配置，读取 hostname、MAC、SSH 端口、root 公钥、系统盘、根分区 UUID、IPv4 和网关。源码没有实际机器的固定配置。制作出的 **prepared 目录仍是这一台机器的私有制品**，不是通用克隆镜像。

## 支持范围

- 目标机和中转机均为 Debian 13 / x86_64；目标机使用 BIOS + GRUB。
- 目标根分区为系统盘上的直接 ext4 分区；支持 virtio、SCSI/SATA、Xen、NVMe 命名。新官方镜像的 root 为分区 1。
- 无单独 `/boot`，无 LVM、RAID、磁盘加密或其他数据分区；同盘官方 BIOS boot / EFI 分区可以保留到写盘阶段。
- 一张直接网卡提供明确的 DHCP IPv4 默认路由；不迁移静态网络、VLAN、bridge、bond 或额外网卡配置。默认路由选择存在歧义时停止。
- 目标机约 2 GiB 或更多内存，暂存时至少 1.2 GiB 可用内存、1 GiB 可用磁盘空间。压缩镜像不超过 900 MiB，raw 不超过 4 GiB，系统盘不小于 raw。
- 目标机已能使用 root 公钥 SSH 登录；所保留的公钥必须由你确认可信且持有对应私钥。
- 当前运行内核及 `/lib/modules/` 完整，已安装 initramfs-tools、GRUB、systemd-networkd、nftables 等必要工具。缺少时暂存脚本停止，不会联网补装。
- 中转机有至少 4 GiB 可用空间、Python 3，能访问 Debian 官方镜像和软件源。

DD 会覆盖检测到的整个系统盘。写盘前的普通错误会尝试回到旧系统，写盘开始后不能依赖旧系统回滚。无 VNC 时，内核引导或网络故障仍可能导致失联。

## 1. 从目标机导出配置

将源码中的 `collect-target.sh` 和 `profile.py` 一起复制到目标机，例如 `/root/po0-tools/`。在目标机执行：

```bash
bash /root/po0-tools/collect-target.sh /root/po0-target
```

这一步只读系统配置，另创建私有输出目录，不安装软件、不修改系统配置或引导。输出目录必须不存在，包含：

- `target-profile.json`：检测到的目标配置。
- `po0-authorized_keys`：当前 root 的公钥文件，不包含 SSH 客户端私钥。

配置了多个 SSH 端口时，优先选择本次 SSH 连接使用的端口；无法判断时可指定已配置的端口，例如 `--ssh-port 2222`。示例值不代表任何实际机器。

通过现有 SSH/SFTP 将整个 `/root/po0-target/` 复制到中转机，命名为 `/root/po0-official/target/`。不要公开上传此目录。服务器名、IP、MAC 和公钥也会暴露机器信息。

## 2. 在中转机准备官方镜像

在中转机 `/root/po0-official/` 保留从 [Debian 官方 cloud images](https://cloud.debian.org/images/cloud/trixie/latest/) 获取的：

- `debian-13-generic-amd64.qcow2`
- 同批次的 `SHA512SUMS`

原始文件可复用。脚本按 SHA512SUMS 校验下载内容；这里依赖你通过官方 HTTPS 获取校验文件，并没有另行验证 OpenPGP 签名。准备过程不会修改原始 qcow2。

将完整源码放到 `/root/po0-official/ram-scripts/`。也可在 `/root/po0-official/` 解压本项目提供的 `po0-ram-scripts.tar.gz`，目录结构相同。

```bash
bash /root/po0-official/ram-scripts/prepare-image.sh
```

默认读取 `/root/po0-official/target/`；如需其他位置，传入采集结果目录：

```bash
bash /root/po0-official/ram-scripts/prepare-image.sh /root/my-target-profile
```

输出是 `/root/po0-official/prepared/`。目录已存在时拒绝覆盖；换机器制作前，应自行归档旧目录。脚本使用 raw 工作副本及 loop 设备，不写入中转机的系统盘。

此文件是 qcow2 格式，必须先转换为 raw。扩展名不会让 qcow2 的容器数据自动变成可引导的磁盘内容。

镜像会保留官方基础系统和内核，并作以下定制：

- 移除 cloud-init、cloud 辅助工具、netplan 和 qemu-guest-agent；不添加厂商源、探针或业务服务。
- 使用 Debian 官方软件源，安装/保留 SSH、systemd-resolved、nftables、vim、curl、wget、unzip、ca-certificates 及必要系统组件，不执行 `autoremove`。
- 按采集到的 MAC 配置 systemd-networkd DHCP，保留采集到的 hostname。
- 按采集到的端口提供 root 公钥 SSH；禁用密码认证，为这一份镜像生成新的 SSH 主机密钥。
- nftables 默认拒绝入站和转发，允许回环、已建立连接、ICMP、DHCP 响应和采集到的 SSH 端口；出站允许。主机规则不另加来源 IP 白名单，云侧白名单、端口限制不会因此改变。

最终镜像经过这些定制，内容不再与下载的官方文件逐字节相同。替换客体系统也不能移除云平台在宿主机、虚拟磁盘和网络层面的控制能力。

## 3. 打包并私下传输到目标机

在中转机执行：

```bash
bash /root/po0-official/ram-scripts/bundle-for-po0.sh
```

将完整 `prepared/` 通过 SSH/SFTP 复制到目标机，目标目录必须为 `/root/po0-official-dd/`。注意不要多嵌套一层 `prepared/`。该目录含镜像中的 SSH 主机私钥，不能通过公开 HTTP 服务或公开仓库分发。

## 4. 暂存、设置单次引导、手动重启

在目标机执行：

```bash
bash /root/po0-official-dd/stage-ram-boot.sh
```

暂存会核对镜像与目标的 hostname、MAC、SSH 端口及公钥，并重新检测当前系统盘、UUID、地址和路由。随后使用当前内核构建 RAM initramfs，再解包检查必要工具和 SSH 配置。此命令不改 GRUB、不重启。

成功看到 `RAM files are ready` 后，再执行：

```bash
bash /root/po0-official-dd/arm-dd.sh
```

此命令再次检查目标状态和文件校验和，设置 `grub-reboot` 单次引导，仍不重启。只有之后执行 `reboot`，才会进入 RAM 环境并自动覆盖检测到的系统盘。

重启前取消：

```bash
grub-editenv /boot/grub/grubenv unset next_entry
```

RAM 环境保留旧系统的 SSH 主机密钥、root 公钥和端口；优先 DHCP，并复用 [reinstall-Po0](https://github.com/mihomoQ/reinstall-Po0) 的 MAC 识别和缺失地址恢复函数，详见 [UPSTREAM.md](UPSTREAM.md)。进度查看：

```bash
cat /run/po0-dd.status
tail -n 50 /run/po0-dd.log
```

流程是：镜像复制到 RAM → 卸载旧根分区 → 校验完整 raw → 写盘 → 完整读回校验 → 离线扩容 GPT root/ext4 → 重启。不安装额外的首次启动扩容服务。

写盘前出错会尝试把日志保存到旧系统 `/root/po0-dd-failure.log` 后重启；写盘开始后出错则保留 RAM 和临时 SSH，停止自动重启。GRUB 单次启动不能保证写盘失败后仍能启动旧系统。

新系统的 SSH 主机指纹应与中转机 `prepared/SSH_HOST_ED25519.pub` 一致，核对后再更新 known_hosts。检查新系统：

```bash
cat /etc/os-release
uname -r
df -h /
systemctl is-active ssh systemd-networkd systemd-resolved nftables
```

## 镜像复用和旧版兼容

| 保留的文件 | 可以怎么复用 |
| --- | --- |
| 官方原始 qcow2 和对应校验文件 | 作为不同机器的制作起点；每台机器重新采集配置、定制镜像并生成各自的主机密钥。 |
| 通用版生成的完整 prepared 目录 | 可用于同一台机器、相同 hostname/MAC/SSH 端口/公钥的再次重装。UUID、盘符、容量和 DHCP 租约可以变化，暂存时重新读取。仍须满足支持范围和容量条件。 |
| 之前专用版生成的 debian13.raw.xz | 留作原机器的私有备份。它没有新版配置元数据，不可只补一个 profile 就冒充新版镜像；新版脚本明确拒绝。应保留配套旧脚本，或从原始 qcow2 制作新版。 |
| 只有 debian13.raw.xz，没有配套元数据 | 不直接使用此流程。需要匹配的 raw 大小、校验和、主机指纹和配置记录。 |

成品镜像会恢复制作时的软件和配置快照，包含当时的登录公钥及 SSH 主机私钥。公网 IP/租约变化本身不要求重做镜像；MAC、hostname、SSH 端口或公钥变化需要重新制作。不要将同一成品镜像直接复制给不同机器，也不要公开发布它。

## Git 和隐私

可公开的是源码。`.gitignore` 排除了镜像、压缩包、prepared/、target/、采集 JSON、公钥文件、私钥、运行日志及引导元数据。忽略规则不能清除已经提交的历史，也不能防止 `git add -f`。

本仓库的测试使用虚构主机名、文档 IP 和本地管理 MAC，不包含真实目标配置。此前带机器固定值的本地 Git 历史已清理，当前历史从去标识化版本重新开始。准备制品应始终存放在仓库之外或保持为忽略文件。

## 验证

此前专用版已由用户在真实 Po0 成功验证：官方 Debian 13 启动、整盘写入和读回校验、GPT/ext4 扩容、公钥 SSH、网络与 nftables。通用版是在该流程基础上改为采集配置；它没有再次在用户的真实机器上重装。

本版本的自动检查见 [tests/README.md](tests/README.md)：配置采集和拒绝条件、同机镜像复用、实际 SSH 登录、nftables 解析、DNS 文件权限、APT 失败停止，以及 QEMU 中的离线写盘/扩容和写前校验失败恢复。测试只覆盖说明的布局，并非所有云厂商和硬件的成功保证。
