# 扩展离线验证发现的最小复现

产品源码仍为 `56ac0e5`，不含本轮产品修复。每项都有执行中的测试，三项 `--runxfail` 输出保存在 [raw-known-defects.txt](raw-known-defects.txt)。以下命令中的 Python 指隔离环境的 Python，在仓库根目录运行。各项退出码 **1** 是缺陷复现成功，而不是验收通过。

## OFFLINE-001：convert 提升后生成不可调用名称

输入手写 Python（lifting 按 AST 读取，不执行输入模块）：

```python
def narrowed(value: REAL) -> DINT:
    return convert(value, "DINT")
```

流程：`lift_source` → `prepare` → `emit_st` → `parse_source` → `prepare` → `emit_python` → 执行 `narrowed(2.9)`。

期望来自项目 runtime 的 `convert` 契约：2.9→2、-2.9→-2。实际 ST 调用 `TO_DINT(value)`，返回 Python 仍调用 `TO_DINT`，运行报 `NameError: name 'TO_DINT' is not defined`。各阶段无错误诊断。

源码证据：`frontends/python/lifter.py` 的 `_call` 对 `convert` 构建 `TO_{target}`；`backends/python/emitter.py` 用包含 `_TO_` 判断转换调用，`TO_DINT` 不满足该模式。尚未决定最适合在哪个阶段修复。

```sh
"$EXT_PYTHON" -m pytest --runxfail -v --tb=short tests/test_expected_logic.py::TestLiftedHandWrittenPython::test_lifted_conversion_matches_independent_expectation
```

## OFFLINE-002：C++ 对前向 FB 依赖未排定义顺序

输入 ST：

```iecst
PROGRAM Factory
VAR child : Cell; total : DINT; END_VAR
    child();
    total := child.total;
END_PROGRAM
FUNCTION_BLOCK Cell
VAR_OUTPUT total : DINT; END_VAR
    total := total + 1;
END_FUNCTION_BLOCK
```

解析、链接、类型检查没有错误，Python 执行一个扫描得到 total=1。生成 C++ 在 Factory 中声明 `Cell child;`，但 Cell 定义在 Factory 之后，编译报 `unknown type name 'Cell'`。

源码证据：`backends/cpp/emitter.py` 直接按 `project.pous` 顺序逐个输出定义，没有为此示例提前定义依赖块。最初通过逆依赖加载多文件合成项目发现；最终测试缩减为两个 POU 的单文件示例。尚未修改文件顺序、IR 或 emitter 来修复此缺陷。

```sh
"$EXT_PYTHON" -m pytest --runxfail -v --tb=short tests/test_offline_differential.py::test_cpp_forward_function_block_dependency
```

## OFFLINE-003：函数往返生成自赋值，严格 C++ 编译失败

输入 ST：

```iecst
FUNCTION Increment : DINT
VAR_INPUT value : DINT; END_VAR
    Increment := value + 1;
END_FUNCTION
```

流程：ST→Python→lift→ST→重新解析→C++。返回 Python 的 `Increment(2)` 能得到 3，但 C++ 函数中多出 `_result = _result;`，Apple clang 21.0.0 在 `-std=c++17 -Wall -Wextra -Werror -O1` 下报：

```text
error: explicitly assigning value of variable of type 'plcforge::DINT'
(aka 'int') to itself [-Werror,-Wself-assign]
```

该问题属于往返后严格构建失败；当前证据不表明自赋值本身改变数值结果。本轮没有去掉 `-Werror` 或关闭告警。原始 ST 直接 C++ 的合成工程能通过相同严格编译。

```sh
"$EXT_PYTHON" -m pytest --runxfail -v --tb=short tests/test_offline_differential.py::test_roundtrip_function_cpp_is_warning_free
```

## 跟踪规则

三个复现分别使用 `strict=True` 和限定异常类型的 xfail，编译失败与运行失败分开处理。修复后严格 XPASS 会使测试失败，届时应移除 xfail 并重新验收，不应长期保留为豁免。

这些缺陷已在本轮报告中交总管决策。完整范围、测试设计和未验证项见 [offline-expanded.md](offline-expanded.md)。
