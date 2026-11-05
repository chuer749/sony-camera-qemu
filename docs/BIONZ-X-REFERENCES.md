# BIONZ X 采集与仿真实现对照

研究日期：2026-11-01。仅阅读公开文档、源码和维修 PDF，没有操作相机。下述实机结果均是来源作者报告，未在本机复现。

## 最值得复用的三组工程

### dino2048/zv-e10-toolkit

- [中文 Linux 工具说明](https://github.com/dino2048/zv-e10-toolkit/blob/main/linux/README.zh-CN.md)：作者报告 ZV-E10 固件 `30110_05.2025031501`、macOS 实测。
- [共用连接实现](https://github.com/dino2048/zv-e10-toolkit/blob/main/linux/host/zve10_common.py)：直接调用 `senserShellCommand(driverName='libusb', complete=...)`；通过 `LIBUSB_PATH`、Homebrew 路径选择动态库。不是自行重写认证算法。
- [setup.sh](https://github.com/dino2048/zv-e10-toolkit/blob/main/linux/scripts/setup.sh) 固定 PMCA 提交 `a82f5baaa8e9c3d9f28f94699e860fb2e48cc8e0`，与本项目引用的协议提交一致。
- [FileControl 说明](https://github.com/dino2048/zv-e10-toolkit/blob/main/linux/docs/filecontrol-read.zh-CN.md)：用 `0xff01` 的 READ=2，传递补齐到 4 字节边界的文件路径，流式接收整个对象；读取本身不需要上传助手或开启终端。
- [原始备份程序](https://github.com/dino2048/zv-e10-toolkit/blob/main/linux/host/zve10_backup_raw.py)：实际程序先启用终端采集分区／挂载／容量元数据，然后关闭终端，再双遍读取 Linux 可见用户区及 B0/B1；校验精确大小、接收哈希、落盘后重读哈希，并记录差异。
- 该版本代码把预期固件和三个区域容量写死为 ZV-E10 基线，不能原样对 IIIA 运行。整用户区包含实时变化的设置／日志，两遍不一致不自动等于坏备份。
- [服务说明](https://github.com/dino2048/zv-e10-toolkit/blob/main/linux/docs/service-mode.zh-CN.md) 提到认证可能更新相机自身 `.etools`／`.hasp`；主机程序只发送 READ 不代表相机内部绝无状态变化。

适合本项目：连接时动态库选择、上游原版会话封装、固定 READ 采集、分块流式保存、双遍与落盘核对。BootROM、终端执行助手和写回恢复是独立路线，不是读取分区的必要前置。

### cokeenet/zve10-qemu

- [项目](https://github.com/cokeenet/zve10-qemu) 针对 ZV-E10 一代、CXD90045、ARM32，包含机器模型、分区映射、模块／用户态补丁、启动脚本和排错记录。
- 作者报告内核、多个模块和用户态应用运行有进展，显示链仍未完成；本项目未验证这些运行结果。
- [启动脚本](https://github.com/cokeenet/zve10-qemu/blob/main/scripts/run_qemu_zve10.sh) 使用 `cxd90045`、自己的 cmdline、内核和 eMMC，并额外装载备份数据。它不是把 ZV-E10 固件直接丢进普通 QEMU。
- 历史 [HANDOFF_2026.md](https://github.com/cokeenet/zve10-qemu/blob/main/docs/HANDOFF_2026.md) 记载部分 abort 被改为自旋；因此“进程仍活着”“零 abort”不等于原生应用行为正确。使用时要区分历史快照与最新状态。
- [机型对照表](https://github.com/cokeenet/zve10-qemu/blob/main/data/machine_comparison.tsv) 可用于比较 A6400、ZV-E10、ZV-1F 的镜像结构；它是作者派生记录，不是 Sony 硬件规格保证。

适合本项目：同平台 QEMU 补丁与模块排错方法，尤其是 A7M3 参考工程未覆盖的用户态／图形设备线索。镜像偏移、内存、GPU 设备和补丁应分别适配。

### qn06142/zv-e10-re

- [项目](https://github.com/qn06142/zv-e10-re) 研究 OSAL/UIPC、UI 资源和 `av-cam.bin` 图像处理链，适合后续离线定位 JPEG／视频处理路径。
- [USB 驱动](https://github.com/qn06142/zv-e10-re/blob/main/pmca/usb/driver/generic/libusb.py) 对 Windows Zadig/libusb-win32 设备优先选择 libusb0，避免能枚举却不能 claim 的情况；这是 Windows 特定路径，不是本机 macOS 修复。
- 项目同时含内存和持久修改方案；本次未运行任何脚本，也没有验证其功能解锁声明。

## 其他机型成功／失败记录

- [A6400 issue #513](https://github.com/ma1co/Sony-PMCA-RE/issues/513)：切入服务模式后出现第二个 USB 身份，Windows 用户需为服务设备再绑定一次 libusb 驱动。与“第一笔认证尚未写出就 I/O error”不是相同阶段。
- [A6400 issue #733](https://github.com/ma1co/Sony-PMCA-RE/issues/733)：作者报告多个 Windows 驱动组合下，已认证后的 `BACKUP_PDT_READ` 批量命令都造成硬 USB 故障；单属性路径可以工作。作者关于分块／时序的解释仍是假设。这说明部分失败也可能来自命令或相机行为，不能把所有断线归因于主机系统。
- A7M3 原参考项目直接通过 FileControl 读取分区，不要求 Jiritsu 路径或预加载库。

## 为什么不能只有一个 BIONZ X 配方

[fwtool 的 devices.yml](https://github.com/ma1co/fwtool.py/blob/master/devices.yml) 将 A7 II／R II／A6300 等列为 CXD90014，将 A7 III／R III／A7C／A6400／A6600／ZV-E10／ZV-1 等列为 CXD90045。Sony 的 BIONZ X 名称跨多个硬件平台，不能替代 SoC、固件格式和版本识别。

建议保持一个统一产品入口，按“机型身份 + 硬件平台 + 固件内容指纹 + 主机 USB 实现”选择适配数据。USB 采集与离线构建共享流水线；设备路径、容量、补丁和显示模型分别配置。

当前 IIIA 的 CXD90045 身份仍是候选线索，没有因本次跨机型研究而变成已验证事实。

## 维修手册查阅结果

- 已下载 [ZV-E10 维修手册](https://research.encompass.com/SON/rr/ZVE10.pdf)，135 页、45,756,853 字节；它是图像为主的 PDF。本次视觉核对前五页的封面、服务说明、拆装说明，不能声称已逐页阅读。
- 已下载 [A7M3 2023.12 维修手册](https://research.encompass.com/SON/rr/ILCE7M3.pdf)，85 页、17,637,723 字节；完成文本提取与相关术语搜索。内容主要是 Level 2 拆装与零件资料，没有在该文本中找到服务认证报文说明。
- 另核对 [A7M3 2018.04 旧版手册第 6 章](https://www.manualslib.com/manual/2387981/Sony-Ilce-7m3.html)：调整工作另需 Adjust station 和按机型配套的 Adjust manual 软件。不能把两份不同版本的手册混为同一文档。
- 找到 A7R III 手册公开索引，但 PDF 下载遇到站点 403；没有取得可核验的 IIIA 专属完整调整资料。普通用户帮助指南不作为维修认证协议证据。
- 未取得或执行 Adjust station／Adjust manual 安装包；手册里的服务工具名称不构成现成的 USB 认证修复。

本次查阅的 PDF 与源文件快照未纳入公开归档。

## 对当前故障的更新判断

ZV-E10 + macOS + 同版 PMCA 的公开实现说明：macOS 服务采集有可行先例，不能认定必须换 Windows。优先对照该实现的动态库选择、原版会话路径、阶段时序和错误处理。

但该作者没有给出我们当前 macOS／libusb 组合在 IIIA 上的运行证据，故仍不能宣称已找到重启的根因。父目录实机停止标记保持原状。
