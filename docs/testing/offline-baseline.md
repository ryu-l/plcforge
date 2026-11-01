# PLCForge 离线测试基线报告

- 日期：2026-10-28
- 源码版本：v0.4.0，commit `56ac0e5`（"Escape backslashes when embedding a source path in generated code"），工作树干净（`git status` 无未提交改动）
- 报告位置：本文件；原始测试输出见同目录附件（`raw-*.txt`）

## 1. 运行环境

| 项目 | 值 |
|---|---|
| 机器 / 系统 | macOS arm64（Apple silicon），darwin 25.4.0 |
| Python | 3.13.12（`/Users/mac/miniconda3/bin/python3`） |
| 测试框架 | pytest 9.2.0 + pytest-cov 7.1.0（pluggy 1.7.0） |
| C++ 编译器 | Apple clang 21.0.0（`c++`/`clang++`/`g++` 均在 `/usr/bin`） |
| 隔离方式 | 独立 venv：`/private/var/folders/.../T/opencode/plcforge-baseline-venv`（仓库内未创建任何环境，未改动用户原有 Python 环境） |

安装命令（在仓库根目录）：

```bash
python3 -m venv <隔离目录>
<隔离目录>/bin/pip install --upgrade pip
<隔离目录>/bin/pip install pytest pytest-cov
<隔离目录>/bin/pip install -e ".[dev]"
```

`pip install -e ".[dev]"` 一次性装入了 pytest、pytest-cov、ruff、mypy（`pyproject.toml` 的 dev extras）。本机 conda Python 无 pytest，因此不装 venv 无法直接运行套件——这是本环境唯一发现的"依赖遗漏"，用隔离 venv 解决，未触碰用户环境。

## 2. 执行命令与总体结果

全部命令在仓库根目录、上述隔离 venv 下执行：

| # | 命令 | 结果 |
|---|---|---|
| 1 | `pytest -v -ra --tb=short` | **198 passed，0 failed，0 skipped**，4.38s（见 `raw-pytest-run1.txt`） |
| 2 | `pytest -q -ra`（复跑） | **198 passed**，3.65s（见 `raw-pytest-run2.txt`，两次结果一致，可复现） |
| 3 | `pytest --cov=plcforge --cov-report=term-missing` | 198 passed，总覆盖率 **80%**（5169 stmts / 2124 branch）（见 `raw-coverage.txt`） |
| 4 | `ruff check .` | All checks passed |
| 5 | `mypy` | Success: no issues found in 35 source files |
| 6 | 等价于 `make examples`：`plcforge --strict check examples --summary` + 4 条 `convert` + 1 条 `lift` + `scaffold plant` | 全部成功，8 个 POU 通过 `--strict` 检查并生成 Python/C++/ST/脚手架输出 |
| 7 | 等价于 `make cpp`：`c++ -std=c++17 -Wall -Wextra -Werror -O2 -Ibuild/cpp -fsyntax-only build/cpp/conveyor.hpp` | 通过，`-Werror` 下无告警（见 `raw-examples-cpp.txt`） |

**跳过情况：无任何跳过。** `-ra` 未报告任何 skip / xfail / xpass。特别是 `tests/test_cpp_backend.py` 的 `requires_compiler`（`shutil.which("c++")`）未触发——本机有 clang，15 项中含 5 项 `TestCompiled` 真实编译并执行生成 C++ 的测试全部实际运行（含 `test_timers_behave_identically_to_the_python_backend` 这一 Python/C++ 交叉比较）。此前"C++ 缺编译器会跳过"的逻辑存在（`tests/test_cpp_backend.py:18-19`），但本环境不满足跳过条件。

失败情况：无。因此本轮无失败根因需要归类（环境/测试/产品）。

## 3. 各测试文件构成（共 198 项）

| 文件 | 数量 | 覆盖主题（依据测试名与实现核实） |
|---|---|---|
| test_runtime_core.py | 36 | IEC 数值语义：整除向零截断、取余随被除数、除零报错、FOR 计数含端点、移位不越宽、循环移位回绕、SEL/MUX/LIMIT 等 |
| test_cli.py | 23 | 命令行：check/convert/lift/scaffold/run 子命令、多文件工程、`--summary`、扫描循环（"25 scans"）等 |
| test_semantics.py | 23 | 类型解析与链接：跨文件用户类型解析、未知类型/成员/参数报告、重复声明、混算加宽与警告、诊断分级 |
| test_st_frontend.py | 19 | ST/SCL 前端：词法、UTF-8 BOM 与 CRLF、解析与诊断 |
| test_python_backend.py | 20 | Python 后端：生成代码可执行（`load_generated` 直接 exec 运行）、全局变量单实例共享等 |
| test_runtime_blocks.py | 17 | 时序状态：TON/TOF/TP、CTU/CTD/CTUD、R_TRIG/F_TRIG、SR/RS、RESET、实例时钟覆盖 |
| test_cpp_backend.py | 15 | C++ 后端：结构 10 项 + 真实编译执行 5 项（`TestCompiled`，与 Python 后端行为对照） |
| test_plcopen_frontend.py | 14 | PLCopen XML（LD/FBD）前端读取 |
| test_roundtrip.py | 6 | 行为级往返：ST→Python→ST、接口保持、ST 输出重解析等价、手写 Python lift 回 ST、REPEAT/WHILE 往返、注释安全 |
| conftest.py | — | `build`/`load_generated`/`SimulatedClock` fixtures；`examples` 目录夹具 |

## 4. 覆盖率基线（`--cov=plcforge`，行+分支）

总计 80%。模块级要点：

| 模块 | 覆盖率 | 评估 |
|---|---|---|
| backends/python/emitter.py | 83% | 主路径覆盖较好 |
| backends/cpp/emitter.py | 78% | 结构+编译测试覆盖主干 |
| backends/st/emitter.py | 77% | 往返重解析测试覆盖 |
| backends/python/project.py | 89% | — |
| **frontends/python/lifter.py** | **64%** | **最薄弱路径**：Python→ST 提升方向大量未覆盖（缺失行集中在表达式/语句提升分支） |
| **backends/naming.py** | **61%** | 重命名/保留字冲突处理未覆盖 |
| frontends/plcopen/reader.py | 74% | LD/FBD XML 读取有 84 行未覆盖（多为错误与边角分支） |
| runtime/scan.py | 73% | 扫描循环部分分支未覆盖 |
| runtime/functions.py | 75% | 标准函数部分分支未覆盖 |
| frontends/st/parser.py | 81% / lexer.py 82% | — |
| ir/validate.py 90% / link.py 96% | — | 语义检查较充分 |
| cli.py | 92% | — |

## 5. 覆盖评估（针对开放问题的第 3 问）

按四个维度评估现有测试对"转换前后逻辑正确性"的支撑：

1. **数值语义：强。** `test_runtime_core.py` 以参数化用例钉住了 IEC 与 Python 语义分歧点（整除、取余、除零、FOR 边界、移位宽度）；`test_cpp_backend.py::TestCompiled::test_integer_division_and_modulo_match_iec` 已将同一语义在真实编译执行的 C++ 上交叉验证。已有 Python/C++ 交叉比较的雏形，但仅限定时器与整除/取模/数组越界，未覆盖全量运算与 REAL 量级。

2. **时序状态：较强。** `test_runtime_blocks.py` 用 `SimulatedClock` 对 TON/TOF/TP/计数器/边沿/双稳态做了 ms 级状态断言，且有 RESET 与实例时钟覆盖测试；C++ 侧由 `test_timers_behave_identically_to_the_python_backend` 交叉。

3. **跨文件/多实例：部分。** 跨文件已有：`test_user_types_declared_in_another_file_still_resolve`、CLI 多文件工程、`test_global_variables_become_one_shared_instance`。**缺口**：同一 FB 多次实例化后各实例状态互相隔离的显式用例未见；扫描循环（`runtime/scan.py` 73%）的长时序行为覆盖偏薄。

4. **输出目标：不均衡。** ST→Python、ST→C++ 覆盖较实（生成代码被真实执行）；Python→ST 走 `lifter.py`（64%）是弱项；PLCopen XML 仅有读取方向（`frontends/plcopen/reader.py`），仓库中无 XML 导出后端，"转回厂商原生工程"在当前代码中不存在该路径，谈不上测试。

5. **往返：行为级而非文本级。** `test_roundtrip.py` 6 项均为"程序仍计算同样的东西"的行为断言（符合其 docstring 声明），含 ST 输出重解析等价与手写 Python 提升。**尚未开展**独立"原 PLC 逻辑预期结果"对照——现有往返自洽性（转换器与自己一致）不能替代与外部预期（原厂商工程行为）的一致性，这正是下一轮独立逻辑用例要补的。

## 6. 本环境可执行 / 不可执行的验证边界

**已在本环境实际执行并通过**：Python 全量单测与集成测试、真实 C++ 编译并执行（clang）、示例工程全目标转换、脚手架生成、lint 与静态类型检查。

**本环境（离线 macOS）不可执行、本报告未记为通过的**：
- 任何厂商原生工程格式（TIA Portal / CODESYS / Studio 5000 等）的导入导出——仓库当前也无对应代码路径；
- 与真实 PLC / Windows 厂商软件的行为等价验证；
- 厂商官方样本工程语料的批量转换（依赖并行研究任务 [#1](todo:0apKrMexisbNXbMGERl_O) 的语料结论，本基线未等待它，两者独立）。

## 7. 下一轮建议（基线之上的增量，本轮未执行）

按"证据指向的薄弱点"排序：

1. **独立预期逻辑用例**：不依赖往返自洽、直接手写预期输入/输出序列的逻辑用例，优先覆盖 `lifter.py`（64%）与 `naming.py`（61%）代表的 Python→ST 方向，以及 REAL/混合运算量级。
2. **扩展 Python/C++ 交叉比较**：现有 `TestCompiled` 只交叉了定时器/整除/取模/数组越界，可按 `test_runtime_core.py` 的参数化矩阵在 C++ 侧对拍。
3. **多实例隔离用例**：同一 FB 两个实例并行走扫描循环，断言状态互不串扰；顺带提升 `runtime/scan.py` 覆盖。
4. **大型工程结构测试**：程序化生成大量 POU/变量的工程（受控合成，非厂商语料），验证解析、链接、生成的时间与正确性随规模的行为——不与厂商格式兼容性混同。
5. **变异测试抽查**：故意在 emitter/runtime 引入典型错误（截断方向、定时器边界、名称冲突），确认套件能抓到——验证测试本身的有效性。

以上均为测试新增，不涉及产品代码修复或重构（遵守本轮约束：基线之外不顺带修改）。

## 8. 附件（原始输出，同目录）

| 文件 | 内容 |
|---|---|
| `raw-pytest-run1.txt` | 第 1 次全量运行（verbose，逐项 PASSED） |
| `raw-pytest-run2.txt` | 第 2 次全量运行（复现性确认） |
| `raw-coverage.txt` | 覆盖率完整报告（term-missing，逐模块缺失行） |
| `raw-lint-typecheck.txt` | `ruff check .` 与 `mypy` 输出 |
| `raw-examples-cpp.txt` | 示例工程全目标转换 + 生成 C++ `-Werror` 语法检查输出 |
