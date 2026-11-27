# reinstall-Po0 参考与复用

来源：https://github.com/mihomoQ/reinstall-Po0

审阅版本：`ba6f6ab398cd2a71514a3ec0f1d00b1a2fc2a0cf`。

`po0-network-upstream.sh` 原样复制自该版本的 `initrd-network.sh`。
RAM 环境以 library 模式加载它，使用 MAC 定位网卡、读取地址/默认路由，
并调用 `restore_missing_addresses`：优先保留 DHCP 结果，仅在临时环境缺失地址或路由时恢复旧值。
这不会把最终 Debian 改成静态网络配置。

GRUB 启动项沿用 upstream 的 `search --file` 动态找内核和 `grub-reboot` 单次引导做法。

本方案使用现有 Debian 内核构建 RAM 环境，独立包含写盘、网络和 SSH 工具。
upstream 的安装阶段有 `apk add` 等联网步骤；这里不执行那些步骤，也不运行其镜像后处理。
最终写入之前制作的官方 Debian 镜像，保持该镜像已经去除的 cloud-init 和厂商源设置。

附带 upstream 的 GPL v3 `LICENSE`。这些可修改的脚本源码随包提供。

## 其他参考

[多发行版调研](docs/distro-research.md) 记录了 bin456789/reinstall、
leitbogioro/Tools 和 Unny984/po0-reinstall，以及上述直接上游的调研版本、
支持范围和适配判断。列为参考不代表已复制其实现，也不扩展本项目的支持范围。
