# 多发行版调研

调研日期：2026-11-27。这里记录上游的支持情况和本项目的适配判断，不代表本项目已经支持这些发行版。当前支持范围仍见[详细说明](details.md)，测试范围见[测试说明](../tests/README.md)。

## 参考项目与版本

| 项目 | 调研快照 | 关注点 |
| --- | --- | --- |
| [bin456789/reinstall](https://github.com/bin456789/reinstall) | [`d9ede3d`](https://github.com/bin456789/reinstall/tree/d9ede3db41ddbf0a026a7109f2d1a469375eaaad) | 多发行版分支、内核选择、镜像处理和文件系统扩容。 |
| [mihomoQ/reinstall-Po0](https://github.com/mihomoQ/reinstall-Po0) | [`ba6f6ab`](https://github.com/mihomoQ/reinstall-Po0/tree/ba6f6ab398cd2a71514a3ec0f1d00b1a2fc2a0cf) | Po0 网络限制下的安装流程，以及本项目实际复用的网络函数。 |
| [leitbogioro/Tools](https://github.com/leitbogioro/Tools) | [`2b95e29`](https://github.com/leitbogioro/Tools/tree/2b95e296be3784ad3a4c5150a8ea9878931e4513) | InstallNET 的安装器、云镜像和 DD 路径。 |
| [Unny984/po0-reinstall](https://github.com/Unny984/po0-reinstall) | [`5217a56`](https://github.com/Unny984/po0-reinstall/tree/5217a56e5751b96db3b50c72958776a79d854ea8) | 与本项目接近的中转制作镜像、RAM 离线写盘方案。 |

这些快照便于以后复查；上游后续更新可能改变支持范围。实际复制的代码及许可证归属单独记录在 [UPSTREAM.md](../UPSTREAM.md)。下列项目的支持清单不等于本项目的支持清单。

## 上游支持什么

- **bin456789/reinstall**：README 列出 Debian 9–13、Ubuntu 18.04–26.04 LTS 和 26.10、Alpine、Kali、RHEL/AlmaLinux/Rocky/Oracle、CentOS Stream、Fedora、Anolis、OpenCloudOS、openEuler、openSUSE、NixOS、Arch、Gentoo、AOSC 等，以及 Windows DD/ISO 安装。不同系统走不同安装流程，不能仅替换镜像地址。[支持表与参数](https://github.com/bin456789/reinstall/blob/d9ede3db41ddbf0a026a7109f2d1a469375eaaad/README.md)，[安装阶段实现](https://github.com/bin456789/reinstall/blob/d9ede3db41ddbf0a026a7109f2d1a469375eaaad/trans.sh)。
- **mihomoQ/reinstall-Po0**：在上述项目基础上适配 Po0，README 同样列出多种 Linux 和 Windows。云镜像有内网及 rsync 下载候选，安装阶段仍包含联网下载、`apk add` 等步骤；不能据此认定所有条目都在同一种 Po0 网络限制下完成过验证。[README](https://github.com/mihomoQ/reinstall-Po0/blob/ba6f6ab398cd2a71514a3ec0f1d00b1a2fc2a0cf/README.md)，[安装阶段实现](https://github.com/mihomoQ/reinstall-Po0/blob/ba6f6ab398cd2a71514a3ec0f1d00b1a2fc2a0cf/trans.sh)。
- **leitbogioro/Tools**：快速开始列出 Debian 13、Kali Rolling、Alpine Edge、CentOS Stream 9、AlmaLinux 9、Rocky Linux 9、Fedora 43、Ubuntu 22.04 和 Windows；具体版本另有参数说明。README 开头的“原系统要求”与可安装的目标系统需要分开看。[README](https://github.com/leitbogioro/Tools/blob/2b95e296be3784ad3a4c5150a8ea9878931e4513/README.md)，[InstallNET.sh](https://github.com/leitbogioro/Tools/blob/2b95e296be3784ad3a4c5150a8ea9878931e4513/Linux_reinstall/InstallNET.sh)。
- **Unny984/po0-reinstall**：脚本实际映射到 Debian 12 bookworm 和 Ubuntu 22.04 jammy。它会禁用 cloud-init、注入网络与登录配置，并改用腾讯 apt 源；与本项目移除 cloud-init、保留官方源的目标不同，不能直接照搬。[rfcrelay.sh](https://github.com/Unny984/po0-reinstall/blob/5217a56e5751b96db3b50c72958776a79d854ea8/rfcrelay.sh)，[po0reinstall.sh](https://github.com/Unny984/po0-reinstall/blob/5217a56e5751b96db3b50c72958776a79d854ea8/po0reinstall.sh)。

## 本项目的适配难度

以下为代码层面的估计，没有完成新增发行版的制作和启动验证。

| 候选目标系统 | 相对难度 | 主要需要适配 |
| --- | --- | --- |
| Debian 12 | 低 | 镜像、发行版代号、软件源、包差异；与现有 Debian 13 路径最接近。 |
| Ubuntu LTS | 中 | 镜像命名与校验文件、内核跟踪包、网络/解析组件、SSH 和服务差异。 |
| AlmaLinux / Rocky / Fedora | 中高 | RPM 包管理、网络管理方式、SELinux、XFS 及分区布局。 |
| Alpine | 中高 | apk、OpenRC、SSH/网络配置及工具路径。 |
| Arch | 中高 | pacman、官方镜像选择、启动和初始化方式。 |
| openSUSE / NixOS / Gentoo | 高 | 文件系统、系统配置模型或安装方式需要独立适配。 |
| Windows | 很高 | 镜像部署、VirtIO 驱动、NTFS、登录和网络配置；较大镜像也不适合直接沿用现有 RAM 预算。 |

可以复用的是配置采集的一部分、GRUB 单次引导、镜像先载入 RAM、写盘前后校验和日志处理。当前代码仍有明确的 Debian 13 约束：

- [prepare-image.sh](../prepare-image.sh) 限定 Debian 13 中转机与镜像，使用 trixie 官方 apt 源和 Debian 内核包名。
- [bundle-for-po0.sh](../bundle-for-po0.sh)、[stage-ram-boot.sh](../stage-ram-boot.sh)、[build-ram-initrd.sh](../build-ram-initrd.sh)、[ram-init.sh](../ram-init.sh) 共同约定 `debian13.raw.xz` 文件名。通用化时要同时处理制品元数据与旧版本兼容。
- 新镜像要求根分区为分区 1、ext4，离线扩容使用 `sfdisk`、`e2fsck` 和 `resize2fs`。XFS、Btrfs 或其他布局需要另加路径。
- 暂存检查要求 OpenSSH 的 `sshd-session`、`sshd-auth` 辅助程序。旧发行版不一定使用这套布局。
- `xz` 来自 Debian 13 中转机，运行库由原系统提供；扩展原系统范围时需要验证 ABI，不能假定复制的二进制到旧系统上就能运行。
- 当前压缩镜像上限为 900 MiB、raw 上限为 4 GiB。新增镜像需要实测大小和内存占用，不能直接承诺适用。

**“安装成 Ubuntu”与“支持从 Ubuntu 发起重装”是两件事。** 前者主要适配镜像制作；后者还涉及原系统的工具、内核模块、SSH 和 RAM 环境运行库。新增目标系统并不会自动扩展原系统范围。

Ubuntu 官方镜像的软件清单可用于逐版核对依赖，不能沿用 Debian 包名：

- [Ubuntu 22.04 manifest](https://cloud-images.ubuntu.com/releases/22.04/release/ubuntu-22.04-server-cloudimg-amd64.manifest)：`linux-virtual`、OpenSSH 8.9、systemd 249；DNS 组件打包方式与较新版本不同。
- [Ubuntu 24.04 manifest](https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.manifest)：`linux-virtual`、OpenSSH 9.6、systemd 255，提供独立 `systemd-resolved` 包。
- [Ubuntu 26.04 manifest](https://cloud-images.ubuntu.com/releases/26.04/release/ubuntu-26.04-server-cloudimg-amd64.manifest)：`linux-virtual`、OpenSSH 10.2、systemd 259。以上链接随官方发布更新，版本仅作调研时的参考。

## 建议顺序与验收

候选顺序：先 Debian 12/13 和 Ubuntu LTS，暂时保留 Debian 13 中转机与现有原系统要求。继续默认 Debian 13 cloud 内核；各发行版使用自己的内核选项和包名。此处仅记录建议，没有开始新增适配。

每个新增目标系统至少需要验证：官方镜像及校验、移除 cloud-init 后 DHCP/DNS 正常、保留公钥和 SSH 端口、nftables 生效、所选内核能够启动，以及完整的 RAM 写盘、读回校验和扩容。测试须使用一次性磁盘，不覆盖用户机器。

公开支持表应区分代码适配、自动验证和真实机器验证。现有版本的验证边界以[测试说明](../tests/README.md)及[详细说明](details.md)为准；上游支持某个系统只能作为调研依据。
