# PLCForge 扩展离线逻辑验证

日期：2026-10-29。承接保留提交 `c120ae584aedcc699fddc72947de250b335afc3c`，产品源码与基线 `56ac0e5` 相同（原始环境日志中 `git diff 56ac0e5 -- src` 为空）。仅修改/新增测试、测试辅助脚本和本报告目录，未修复产品缺陷。

## 结果与验收边界

**扩展验证已执行：全量 225 passed、3 xfailed、0 skipped、0 非预期失败，共 228 项，45.59 秒（含覆盖率）。原有 198 项全部通过。** 相对基线新增 30 项：独立预期 20 项、差分/缺陷复现 6 项、合成工程/长扫描 4 项。3 项严格预期失败代表已确认的产品缺陷，不能计为转换通过。取消 xfail 后，三项均真实失败（`raw-known-defects.txt`，退出码 1）。ruff/mypy 通过。

本轮已重新读取 #2 分支的 `docs/testing/offline-baseline.md`，并比对实际源码和保留进度；沿用前次已完成的基线复现，不重做基线报告。原报告把 C++ 定时器测试称为 Python/C++ 对拍，但该测试实际只运行 C++ 并对比常量字符串。本轮新增对拍确实运行两个后端，分别对照独立预期，再逐扫描比较。

环境沿用之前留下的隔离 venv：Python 3.13.12、pytest 9.2.0、pytest-cov 7.1.0、ruff 0.17.3、mypy 2.4.0、Apple clang 21.0.0。完整环境与退出码见 `raw-environment-and-checks.txt`。

## 保留文件的真实状态

首次执行 `tests/test_expected_logic.py`：15 passed、4 failed，见 `raw-inherited-tests.txt`。纠正了三项测试错误：

- `template` 是 C++ 保留字，但不是 Python 保留字；Python 参数保持 `template`。
- `start AT %IX0.0` 的具名变量保留 `start`，地址是元数据；输出也保留 `lamp`。地址字符串自身的名称映射仍有独立参数化断言。
- 8 连续整除 2 的序列是 8→4→2→1→0，需要 4 次。

余下一项是产品缺陷 OFFLINE-001，拆出单独严格 xfail，数值测试中的其他断言继续执行。没有通过修改产品行为或篡改数值答案消除失败。

## 已完成的验证

| 文件 | 实际验证内容 |
|---|---|
| `tests/test_expected_logic.py` | 手算标识符映射、关键字、碰撞；手写 Python 经 ST 返回后的水箱定时/边沿状态、整数除余、限幅、循环、枚举/结构体；REAL 量级 |
| `tests/test_offline_differential.py` | ST→Python/C++ 与 ST→Python→ST→Python/C++；两个数值矩阵用例、两个双实例时序用例、两个最小 C++ 缺陷复现 |
| `tests/test_offline_synthetic.py` | 受控跨文件项目生成、解析/链接、直接 Python/C++ 和往返 Python 逐扫描独立预期；ScanEngine 分段继续与长扫描 |
| `tests/offline_support.py` | 每阶段检查诊断，实际编译执行 C++；编译 120 秒、执行 60 秒超时；编译失败单独异常类型 |
| `scripts/verify_offline_mutations.py` | 临时副本中的四类注错、未变异对照、真实断言失败判定，完全不修改工作树产品源码 |

数值矩阵：4 个边界输入 + 固定种子 20261029 生成 512 个输入，共 516 行；正/负被除数和除数、零被除数、限幅两侧、WORD 位与、负步长含端点循环、REAL 算式。整除与取余的预期以正数绝对值商和符号独立推导，不调用 plcforge runtime。两个后端各自与预期比较，并互相比对；整数精确，REAL 相对/绝对容差均为 1e-6。范围避免溢出和除零，不将未定义/未选定策略混入比较。

双实例时序：9 个绝对时间点，两个实例的输入/复位不同，手写期望覆盖 TON 的 1499/1500ms 边界、输入撤销、重新启动、R_TRIG 单次脉冲、复位优先级、各自的数组与定时/边沿对象隔离。TIME 容差 1e-9，两个后端与往返路径均实际运行。使用时钟 set 消除累计推进引入的边界歧义。

### 合成工程与长扫描

| 档位 | POU/源文件数 | 每个 Factory 的 FB 实例 | 每个数组长度 / 总元素 | 函数链深度 | 扫描数 |
|---|---:|---:|---:|---:|---:|
| 小 | 13 | 8 | 8 / 64 | 4 | 20 |
| 中 | 81 | 64 | 32 / 2048 | 16 | 40 |
| 大 | 289 | 256 | 128 / 32768 | 32 | 64 |
| 长扫描 | 7 | 2 | 4 / 8 | 4 | 100000 |

每个源文件含一个 POU；每个 Cell 有独立状态和下界为 -2 的环形数组，函数调用链依赖跨文件。工程有两个 Factory 实例，启用模式分别为每 3 扫描停一次、每 5 扫描启一次，逐扫描核对两个实例的总值及整个环形缓冲区校验和。Python 直接与往返版本分别核对；C++ 原始版本实际编译执行并核对相同预期。

独立预期：B 个 Cell、深度 D 时每次启用扫描的增量总和 S=B(B+1)/2+B·D；启用 n 次后 total=S·n。宽度 W 的环形数组保留 k=min(W,n) 个总值，checksum=S·k·(2n−k+1)/2。没有把生成 Python 的结果当作 C++ 的唯一真值。

长扫描：Python 直接/往返各执行 100000 次，以 10ms 模拟周期推进 1000 秒；先 `run_cycles(40000)`，再 `run_for(600.0)`，每次输入读取/输出写入回调核对公式，验证统计扫描数、累计模拟时钟与另一未运行实例不受影响。C++ 直接版本的两个实例也执行 100000 次并逐行核对相同公式。**这是模拟时间验证，不是实时时限证明或整夜墙钟浸泡。**

带覆盖率的本机测量：大档解析/准备约 0.45 秒，Python 生成、往返和行为验证约 29.68 秒，直接 C++ 编译执行约 0.96 秒；长扫描约 3.47 秒。计时只作本机观测，不设性能门槛，未证明渐进复杂度、内存上限或工业规模承载。最终不带覆盖率的扩展复跑为 27 passed、3 xfailed，24.97 秒，数据见 `raw-expanded-selected.txt`。原始全量日志中的 `cpp_and_roundtrip_seconds` 标签是早期计时标签，该阶段实际只编译直接 C++；最终代码已改为 `cpp_seconds`，并区分长扫描的 Python/C++ 变体数量。

**发现约束后采用的边界：**大档使用依赖顺序的跨文件输入；逆依赖顺序的 C++ 编译和函数往返 C++ 的无告警要求单独保留为失败复现，不修改排序或生成源码来绕过缺陷。大型/长扫描的函数往返 C++ 未通过严格编译，因此未计为行为验证通过。数值与定时 FB 不含这些函数返回形态，其往返 C++ 已通过。

## 已确认但未修复的缺陷

详细源码、转换阶段与复现命令见 [defect-reproductions.md](defect-reproductions.md)。

| 编号 | 触发与实际结果 | 预期 | 处置 |
|---|---|---|---|
| OFFLINE-001 | 手写 `convert(value, "DINT")` 提升为 `TO_DINT(value)`；生成 Python 执行报 NameError | 2.9→2，-2.9→-2（本项目 convert 契约） | 严格 xfail，限定 NameError；未修产品 |
| OFFLINE-002 | PROGRAM 使用后声明的 FB；诊断无错误、Python 正常，但生成 C++ 先使用未知 Cell 类型 | 类型定义应可供使用者解析 | 严格 xfail，限定编译失败；未修产品 |
| OFFLINE-003 | 普通函数经 Python→ST 往返，多出结果变量自赋值；clang 在 `-Wall -Wextra -Werror` 下拒绝 C++ | 严格编译无告警，Increment(2)=3 | 严格 xfail，限定编译失败；未修产品 |

xfail 使用 `strict=True`：未来变为通过时会报 XPASS(strict)，必须移除标记后正常验收。它们让原套件保持可用，同时显式呈现缺陷；全量退出 0 不意味着全部扩展场景正确。最终复现 `--runxfail` 运行三项全部失败。报告交总管决定是否另开产品修复任务，本任务不实施修复。

## 测试是否能抓错

未变异对照选取三项新增测试，3 passed。脚本为每个变异创建全新临时源码/测试副本，校验 import 的实际路径，避免误加载 editable 安装；只在副本修改一次匹配文本。使用 JUnit 中的单项断言 failure + pytest 退出码 1 判定命中，超时、导入错误、编译错误、环境失败不计成功。四项均被断言抓住，见 `raw-mutations.txt`。

| 注错 | 断言暴露的变化 |
|---|---|
| 整除从向零截断改为 Python floor | 首行 quotient/remainder 从 (-2,-1) 变为 (-3,3) |
| FOR 去掉端点补偿 | 5+3+1 的 9 变为 8 |
| TON 的 >= 改为 > | PT 边界 ready 仍为 False |
| 名称冲突编号 2 改为 3 | motor_2 变为 motor_3 |

局限：这是四个定向注错的敏感度抽查，不是穷举变异得分，未注错 C++ runtime、所有 emitter 分支、并发 IO 或厂商解析器，也没有证明所有缺陷均会被测试发现。注错脚本只有对照通过且 4/4 命中才退出 0。

## 覆盖率、未验证事项

全量行/分支综合覆盖率 80%→82%；naming 61%→97%，Python lifter 64%→76%，ST emitter 77%→84%，C++ emitter 78%→80%。覆盖率是执行范围指标，包含缺陷复现走过的代码，不等同正确率。ScanEngine 仍为 73%，本轮增强既有正常扫描路径的行为强度，未以覆盖率虚报信号处理/实时循环验证。

未验证：真实 PLC、Windows 厂商软件、任何厂商完整原生工程行为、硬件 IO、实时抖动与任务调度、内存增长/泄漏、整夜墙钟运行、不同 Python/操作系统/编译器矩阵、全部溢出策略。没有新增 PLCopen XML 导出测试：仓库无该产品路径。#1 没有完整真实工程行为证据，本轮合成工程结果不能推断四家厂商兼容性。

## 重跑

在仓库根目录；若隔离 venv 已不存在，可用 Python 3.13 新建一个独立环境并安装 `-e '.[dev]'`。以下 `$EXT_PYTHON` 表示所用环境的 Python 绝对路径；本机实际值见环境日志。

```sh
"$EXT_PYTHON" -m pytest -v -ra -s --tb=short --cov=plcforge --cov-report=term-missing
"$EXT_PYTHON" -m pytest tests/test_expected_logic.py tests/test_offline_differential.py tests/test_offline_synthetic.py -v -ra -s --tb=short
"$EXT_PYTHON" scripts/verify_offline_mutations.py
"$EXT_PYTHON" -m ruff check .
"$EXT_PYTHON" -m mypy
```

C++ 用 C++17、`-Wall -Wextra -Werror -O1`，没有关闭自赋值告警。没有编译器时相关测试会跳过；本机没有跳过。

## 原始输出

- `raw-environment-and-checks.txt`：环境、包版本、源码无改动证据、ruff/mypy。
- `raw-inherited-tests.txt`：保留文件第一次实跑，15 通过/4 失败。
- `raw-order-discovery.txt`：逆依赖工程实验，4 失败/4 通过；为探索输出，不是最终验收。
- `raw-roundtrip-discovery.txt`：改用依赖顺序后，函数往返触发自赋值告警的探索输出，4 失败/4 通过/1 预期失败。
- `raw-expanded-suite.txt`：228 项全量执行与覆盖率，225 通过/3 预期失败。
- `raw-expanded-selected.txt`：最终扩展用例复跑及合成工程计时。
- `raw-known-defects.txt`：取消 xfail 后的三个最小缺陷，3 failed，退出码 1 是预期证据。
- `raw-mutations.txt`：未变异对照与 4/4 注错命中证据。
