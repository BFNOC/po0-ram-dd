# 验证脚本

在源码根目录运行无副作用的配置采集单元测试：

```bash
python3 tests/test-profile.py
for script in *.sh; do bash -n "$script" || exit; done
```

其他检查会修改容器里的 SSH 配置，必须在一次性容器中运行。不要给容器映射真实块设备、SSH 凭据或 Docker socket。源码只读挂载。

```bash
docker build --target configs -t po0-dd-validation:local -f tests/Dockerfile tests
docker build --target ram -t po0-dd-ram-validation:local -f tests/Dockerfile tests
docker run --rm --cap-add=NET_ADMIN -v "$PWD:/work:ro" po0-dd-validation:local python3 /work/tests/check-configs.py
docker run --rm -v "$PWD:/work:ro" po0-dd-validation:local python3 /work/tests/check-preparation.py
docker run --rm -v "$PWD:/work:ro" po0-dd-validation:local bash -c 'cd /work; shellcheck -S warning -x prepare-image.sh common.sh collect-target.sh bundle-for-po0.sh build-ram-initrd.sh stage-ram-boot.sh arm-dd.sh ram-init.sh'
```

QEMU 检查会在临时目录创建两个合成磁盘镜像，只写这些文件；新磁盘内放置证明文件，用于确认写盘后是新文件系统。测试不会下载或使用用户的实际系统镜像。

```bash
docker run --rm --device /dev/kvm -v "$PWD:/work:ro" po0-dd-ram-validation:local python3 /work/tests/check-ram-vm.py
docker run --rm --device /dev/kvm -v "$PWD:/work:ro" po0-dd-ram-validation:local python3 /work/tests/check-ram-vm.py --scsi
docker run --rm --device /dev/kvm -v "$PWD:/work:ro" po0-dd-ram-validation:local python3 /work/tests/check-ram-vm.py --bad-raw-hash
```

没有 `/dev/kvm` 时去掉对应参数，QEMU 会回退软件模拟，可能超过测试的时间限制。

覆盖范围：virtio 旧 root 分区 1、SCSI 旧 root 分区 2，替换后均以新 root 分区 1 扩容；2 GiB RAM、DHCP、真实 root 公钥 SSH、整盘校验和读回、GPT 与 ext4 扩容。失败用例验证 raw 校验不匹配时未覆盖磁盘、保留旧分区、保存失败日志和重启。

未在此测试中覆盖 UEFI、NVMe/Xen 实际引导、云控制台、网络中断、供电中断或真实官方 qcow2 的在线包安装。配置采集单元测试覆盖 NVMe 路径解析，但不代表 NVMe 启动已完成实测。
