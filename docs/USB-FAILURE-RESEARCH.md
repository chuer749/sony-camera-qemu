# IIIA USB 服务连接问题：离线证据摘要

状态：Archived / Inconclusive。本文总结归档前的调试判断；原始 USB 报告、认证相关数据和设备标识均未发布。

## 本地历史观察

- 一轮服务连接测试在服务启动之后、第一笔认证数据传输时失败；该轮不能证明认证密钥或摘要算法有误，因为客户端尚未取得认证回应。
- 另一轮使用参考会话入口的尝试更早停在 macOS USB 接口创建／接管阶段，未进入服务认证或文件读取。
- 两轮发生在不同阶段，不能合并成同一个已证实根因。适配器实现、USB 后端、系统版本和相机侧行为都仍可能相关。

## 外部对照

- [PMCA issue #677](https://github.com/ma1co/Sony-PMCA-RE/issues/677) 报告了 macOS 上相似的首笔认证传输失败症状；不是同型号复现，也没有证明根因。
- [libusb ChangeLog](https://github.com/libusb/libusb/blob/master/ChangeLog) 中不同版本包含不同的 Darwin 修复，不能据此推断升级一定解决本机问题。
- [PMCA-RE RELOADED 发布记录](https://github.com/masterflai/pmca-re-reloaded/releases/tag/v1.6.0-rc.1) 报告调整 macOS 接口获取路径；实现和测试设备与本机不同。
- [ZV-E10 工具](https://github.com/dino2048/zv-e10-toolkit) 提供相机连接及文件读取的公开参考实现，但其固件、路径和环境条件不能直接套用于 A7R IIIA。

## 结论

尚未确定 IIIA 服务连接失败的根因，也没有验证修复。任何未来的实机测试都需要单独授权并进行离线审查；该仓库中的 QEMU 研究不需要连接相机。
