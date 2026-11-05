# E001：多机型配置注册与能力记录

状态：Planned；尚未实施，排期后置。任务 Owner 均为 E001。当前先推进 [R3A 启动验证](../validations/V002-r3a-boot.md)，本项不作为 R3A 启动前置条件；轮到通用化阶段时，按 R3A 实际实现复审本契约。

## 目标、范围与依赖

给已有机型枚举、固件导入和构建提供统一配置加载与校验。包含新旧配置兼容、能力元数据、调用点接入及打包。USB、配方拆分、QEMU 设备、新命令和新的固件匹配规则由后续工作负责。

依据：[总计划](../DEVELOPMENT-PLAN.md)、现有 `sony_vm.py`、两份机型 JSON 和 `tests/test_offline.py`。无需相机或转储即可开始，A7M3 精确指纹限制保留。

## 技术契约

新增 `profile_registry.py`，提供单项加载、枚举、结构校验和能力查询。保留 `sony_vm.profile()` 包装及现有 Python 3.9／标准库基线。

接口约定：`load_profile(directory, profile_id)` 返回规范化字典；`list_profiles(directory)` 按 ID 排序返回全部配置；`get_capability(profile, name)` 返回该项记录或默认未知记录。结构错误抛出带配置标识的 `ValueError`，不静默跳过。加载不修改输入文件，调用者拿到的记录不共享可变默认对象。

| 字段 | 约定 |
| --- | --- |
| `id / model / firmware / status` | 非空字符串；ID 使用现有字符集并与文件名一致 |
| `recipe / machine` | 非空字符串或 null；候选允许 null |
| `limitations` | 字符串列表 |
| `schema_version` | 整数 1；旧配置缺失按 1；布尔与未知版本拒绝 |
| `capture_adapter` | 可选非空字符串或 null，缺失按 null；只记录引用，不执行 |
| `capabilities` | 可选映射；缺失能力查询为 `not-tested` |

能力键用小写字母、数字、下划线；示例：`identify/capture/build/kernel/shell/display/input/imaging`。每项含 `state` 与 `evidence` 字符串列表。状态限 `not-tested/reported/passed/failed`，后三者须有非空证据引用；加载器仅校验结构，不验证证据真实性。

保留配方专用字段和旧 `status`。能力元数据不改变自动匹配、不绕过 `preflight()`，也不从型号推断硬件或支持范围。

以下为结构示例：

```json
{
  "capabilities": {
    "shell": {"state": "reported", "evidence": ["third_party/a7m3/STATUS.md"]},
    "capture": {"state": "not-tested", "evidence": []}
  }
}
```

## E001-T01：配置加载与校验

- Status：Planned（排期后置）。
- Goal：新旧配置通过同一加载器形成一致结果。
- Scope：新增 `profile_registry.py`、`tests/test_profiles.py`；不改 CLI、原配置及构建逻辑。测试可传临时配置目录。
- Dependencies：无代码依赖；先读现有 `profile()` 与两份 JSON。
- Verification：旧配置关键字段不变；覆盖非法 ID、文件名不符、无效 JSON、字段类型错误、未知 schema、无证据的通过声明、合法 null、缺省能力。用本地或临时文件，无 USB。
- 学习：JSON、字典、校验；解释“配置合法”与“机型已支持”的区别。

## E001-T02：接入入口与打包

- Status：Planned。
- Goal：所有现有配置调用点使用统一规则且行为兼容。
- Scope：`sony_vm.py` 的单项读取、枚举、自动匹配；`engine/Dockerfile`、`.dockerignore` 的模块打包及必要回归。保留函数包装、文本输出、退出码与指纹规则。
- Dependencies：T01 验收通过。
- Verification：`profiles/doctor` 和离线回归通过；候选、错误固件仍拒绝构建；能力声明不改变自动匹配。构建器中 `prepare_source.py` 导入 `sony_vm` 时能找到新模块。
- 学习：模块导入、兼容性、容器文件边界。

## E001-T03：迁移配置与交付候选示例

- Status：Planned。
- Goal：他人有可沿用的机型登记范例，现有支持范围准确。
- Scope：两份现有配置补 schema，按已有证据保守填写能力；新增 `docs/examples/candidate-profile.json`，仅为无采集／构建配方的占位示例，不放进生产 `profiles/`。不更改硬件身份、版本、补丁数据或添加未经核实的真实机型版本。
- Dependencies：T02 验收通过；容器验收需 Docker 可用。
- Verification：示例复制到临时配置目录可加载与枚举，能力未知且仍不能构建；错误指纹不被能力声明放行。运行下方回归并记录更新后镜像检查结果。
- 学习：登记配置、实现适配、验证功能各自承担什么责任。

## 验证与完成标准

从 `sony-vm/` 执行。第一条在 T01 实现后才有对应测试；这里列执行方法，不代表已通过。

```sh
python3 -m unittest discover -s tests -p 'test_profiles.py' -v
python3 -m unittest discover -s tests -p 'test_offline.py' -v
python3 sony_vm.py profiles
python3 sony_vm.py doctor
```

T03 末尾做一次更新后容器验收；构建需要 Docker 与网络：

```sh
docker build -f engine/Dockerfile -t sony-vm:e001 .
docker run --rm --network none sony-vm:e001 doctor
docker run --rm --network none sony-vm:e001 profiles
```

三项任务有实际证据、旧行为与校验保留、候选不误报支持、宿主机及容器均可加载，才记 E001 完成。环境不足保留未验证项。任务状态和必要验收结果维护在本文件，不另造进度报告。

## 执行方式

默认教学模式：一次一个 Task，先解释，再由用户亲手编写或指示 AI 修改；验证和回顾后停在当前任务边界。本包当前不执行；R3A 阶段推进后再复审并安排 T01。

以后明确使用 `work-item-manager` 时，仅接管 E001；Manager 只更新状态和验收结果，代码由 Worker 实施，不能修改本契约或自动推进 V001 和后续工作。
