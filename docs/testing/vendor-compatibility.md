# 四家 PLC 厂商工程文件与导出格式的离线兼容性

- 调查日期：2026-10-28
- 代码基线：plcforge 0.4.0，提交 `56ac0e5a503ea1ba7d01ea554d70afee34a2d838`
- 范围：西门子、欧姆龙、汇川、施耐德；只核实工程文件、导出格式、现有读取与转换能力，不新增解析器，不修改产品逻辑。
- 环境限制：没有四家厂商的工程软件、运行时或控制器。本次取得并实测了西门子 SimaticML、施耐德 Control Expert XEF 和标称汇川 InoProShop 的 PLCopen XML 工件，但没有四家齐全、覆盖完整工程的同源真实样本。因此，本文严格区分官方资料结论、源码静态结论和本地实测结论。

## 1. 结论摘要

1. **当前 plcforge 没有四家厂商原生工程或归档的专用读取入口。** 不能把 `.xml` 后缀、ZIP 可解压或文本可查看等同于“工程可打开”。
2. **现有可行入口是通用 SCL/ST 与 PLCopen XML，而不是任意厂商 XML。** 西门子 SimaticML 与施耐德 Control Expert XEF 都必须和 PLCopen XML 分开判断。
3. **真实工件结果已经分化，但四家均无厂商行为等价证据。** 标称 InoProShop V1.9.1.6 的 PLCopen XML 可提取 9 个 ST POU 并生成 Python（37 个警告），但 439 个扩展全局变量和 2 个扩展任务未进入 IR；西门子 V19 SimaticML 与施耐德 V15.0 XEF 均为 0 POU/`PF002`；欧姆龙仍无真实导出样本。
4. **本地通用回归通过，但发现 XML 基线缺口。** 198 项测试全部通过；自带 LD/FBD 示例可以转换并运行通用行为断言，但两个 XML 示例均未通过 PLCopen 官方 TC6 XML 2.01 XSD 校验。因此，现有测试通过不等于标准符合性通过，更不等于四家厂商导出兼容。

| 厂商 | 原生工程/归档的当前结论 | 经厂商软件导出后的候选路径 | 本次真实厂商样本实测 |
|---|---|---|---|
| 西门子 | 无专用入口，不能直接打开或转换 | SCL 文本可进入 ST/SCL 前端；SimaticML 不是 PLCopen XML，需要独立适配 | V19 SimaticML 实测：well-formed，但 0 POU，`check`/`convert` 均为 `PF002`、退出 1 |
| 欧姆龙 | 无专用入口，不能直接打开或转换 | 以核实后的 Sysmac Studio XML/ST 导出路径为准；标准 XML 与原生项目分开处理 | 未验证 |
| 汇川 | 无专用入口，不能直接打开或转换 | AutoShop 与 InoProShop 分开；InoProShop PLCopenXML 是已有入口的候选，AutoShop 未确认该路径 | 标称 V1.9.1.6 样本：9 POU、Python 生成/导入通过，37 警告；扩展全局变量和任务丢失，行为未验证 |
| 施耐德 | 无专用入口，不能直接打开或转换 | Machine Expert 的 PLCopenXML 是候选入口；Control Expert XEF 是另一种 XML，需要独立适配 | V15.0 XEF 实测：well-formed；默认 `PF001`+`PF002`，强制 PLCopen 也为 `PF002`，退出 1 |

## 2. 结论口径

### 2.1 四级结论

本文的四个结论层级均针对**当前 plcforge**，不是仅针对操作系统或通用 XML 工具：

| 层级 | 判定含义 | 不能用来替代它的证据 |
|---|---|---|
| 工程可打开 | 能识别输入容器/格式并建立正确的工程语义模型；只解压或只读出 XML 节点不算 | 文件存在、扩展名相似、ZIP 可解压、XML well-formed |
| 程序可提取 | 能从真实样本提取 POU、变量、类型及程序体，并能列出缺失内容 | 官方手册只说明“支持导出”，或人工构造的同构片段 |
| 代码可转换 | 真实样本经现有流水线完成 `check`/目标代码生成，诊断与未转换部分已记录 | 对另一个自制样例转换成功 |
| 行为已验证 | 生成代码已运行并核对预期结果；若声称“厂商行为等价”，预期序列还必须来自对应厂商软件/控制器或可追溯的权威结果 | 只编译、只生成代码、只运行自制样例 |

状态用语：

- **不支持**：源码能够确认没有对应入口或语义适配；这不表示永远无法支持。
- **候选/未验证**：官方资料表明存在可能衔接的导出路径，但缺少符合要求的真实样本或实际转换。
- **未核实**：本次读取的官方资料不足以确认格式或版本边界；不能反向断言厂商不支持。

### 2.2 证据等级

- **文档**：厂商或标准组织官方手册、在线帮助、下载页；记录版本与章节。
- **源码**：对本提交中的读取、IR 与后端实现做静态核实。
- **样本检查**：检查可追溯样本的结构、头信息与内容完整性。
- **实际转换**：在当前环境执行 `load_sources`/`translate` 或 CLI。
- **行为检查**：实际执行生成的 Python；仅对所用测试向量负责。

## 3. 当前实现的共同边界

### 3.1 输入入口

`src/plcforge/pipeline.py:35-43` 只注册以下源扩展名：

| 前端 | 扩展名 | 本次核实结论 |
|---|---|---|
| ST/SCL | `.st`、`.scl`、`.iecst`、`.exp` | 通用词法/语法入口；没有四家厂商方言或版本检测 |
| PLCopen XML | `.xml`、`.plcopen` | 所有 `.xml` 默认进入同一 PLCopen 读取器 |
| Python | `.py` | 与厂商工程导出入口无关 |

`detect_frontend()` 仅按后缀查表（`pipeline.py:70-72`）。本次对常见原生工程/归档后缀做路由检查，均返回 `None`。目录展开时未知扩展名会被直接过滤；若目录因此没有可读源文件，会报 `no source files found`。显式输入未知扩展名且未强制前端时才得到 `PF001`，随后若没有解析出 POU 还会得到 `PF002`；使用 `--frontend` 会绕过后缀判断（`pipeline.py:91-131`）。因此，不能把原生工程与源码导出混在同一“支持 XML/ST”结论中。

### 3.2 PLCopen XML 读取器不是通用厂商 XML 读取器

- `src/plcforge/frontends/plcopen/reader.py:128-162` 只用 `ElementTree.fromstring()` 检查 XML 是否 well-formed，然后查找 `types`、`pous`、`instances` 等 PLCopen 结构；没有根元素、命名空间、XSD 或厂商格式识别。
- `_tag()` 会去掉任意命名空间并统一大小写（`reader.py:75-78`），没有版本白名单；现有测试还明确接受无命名空间与 2.00 命名空间（`tests/test_plcopen_frontend.py:337-348`）。因此，“解析没有立即报 XML 错误”不能证明输入就是 PLCopen XML，未来增加严格识别也必须同步重新界定这些既有测试预期。
- 程序体支持 ST、LD、FBD；IL/SFC 只保留接口并报告 `PF124`，程序体为空（`reader.py:233-261`）。
- XML 数据类型只在 `struct` 分支建立 `StructType`；非结构类型只给出 `PF121` note，文本虽称“translated as an alias”，实际并未建立等价类型节点（`reader.py:164-180`）。note 不会使转换失败，`--strict` 也不会因它失败。ST 别名则会被表示成单字段 `StructType`，并非等价别名语义（`parser.py:299-324`）。真实工程含枚举、别名等类型时必须外部对照盘点。
- 配置与资源仅递归收集 `globalVars`（`reader.py:155-161,182-191`）。没有任务周期、优先级、程序实例绑定或硬件组态的工程级模型；`Project` IR 也只有 POU、结构、枚举、全局变量和来源文件（`src/plcforge/ir/nodes.py:373-382`）。
- PLCopen 2.01 XSD 提供 `addData` 作为应用特定扩展点，但当前 `src/` 没有 `addData` 处理或专门诊断。厂商导出若以扩展保存任务、图形或专有语义，不能把这些内容算作已保留。
- ST 顶层的 `CONFIGURATION`/`RESOURCE` 会直接跳过且无诊断（`src/plcforge/frontends/st/parser.py:267-295`）。XML 变量缺失 `<type>` 时默认 BOOL，`structValue` 也未读取（`reader.py:271-324`）；未知 body 语言还可能留下空程序体而没有 `PF123/PF124`。所以完整性不能只依赖现有诊断。

因此，即使某个厂商导出确为 PLCopen XML，当前能力上限也是“所支持程序内容的转换”，不是完整工程或任务行为复制。

### 3.3 SCL/ST 入口不能自动覆盖厂商方言

ST/SCL 前端支持通用 POU、变量、类型和语句子集，但没有厂商版本识别。实际导出中若出现引号标识、厂商属性、额外部件、受保护块或专有类型，必须以真实导出诊断为准。`docs/language-support.md:16-18` 将 TIA Portal 与 “Export → PLCopen XML” 直接并列的表述，不能作为本次兼容性结论；TIA 的 SimaticML 应单独核实与适配。

## 4. 当前环境实际完成的测试

### 4.1 环境与回归

- Python 3.13.12；隔离虚拟环境中的 pytest 9.2.0。
- `PYTHONPATH=src python3 -m pytest -q`：**198 passed in 5.20s**。
- 这些测试证明当前通用 ST、PLCopen XML、Python/C++ 后端与运行时回归通过；测试语料不是四家厂商真实导出，不提高任何厂商的实测等级。

### 4.2 自带样例的读取、转换与行为检查

实际调用现有 `translate()` 并执行生成的 Python，结果如下：

| 输入 | 实际结果 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| `examples/plcopen/MotorStarter.ld.xml` | 1 个 POU；Python 生成无诊断；自保持、5 s 定时与停止断言通过 | 当前 LD 子集可沿现有路径转换并运行 | 不是厂商导出；未证明厂商图元、任务或运行时等价 |
| `examples/plcopen/TankControl.fbd.xml` | 1 个 POU；Python 生成无诊断；锁存与 3 s 报警断言通过 | 当前 FBD 子集可沿现有路径转换并运行 | 同上 |
| `examples/scl/conveyor.scl` | 3 个 POU、1 个 STRUCT、1 个 ENUM；Python、C++、ST 生成均无诊断 | 当前通用 SCL/ST 子集可进入三个后端 | 文件头没有厂商版本；不是 TIA/Sysmac/AutoShop/InoProShop/Machine Expert/Control Expert 实测 |

两个 XML 的文件头均写明 `companyName="plcforge"`、`productName="plcforge examples"`，所以只能作为项目自制回归样本。

### 4.3 官方 PLCopen XSD 校验

从 PLCopen 官方下载页取得 `PLCopen XML version 2.01 xsd file`，解压得到 `tc6_xml_v201.xsd`，使用 xmllint/libxml 2.13.9 校验。XSD 的 SHA-256 为 `591b92ba65018a77c32ab9e606abf27bd810cb1f7761d972a85689531c51e20f`。

```bash
xmllint --noout --schema tc6_xml_v201.xsd examples/plcopen/MotorStarter.ld.xml
xmllint --noout --schema tc6_xml_v201.xsd examples/plcopen/TankControl.fbd.xml
```

结果：**两个文件均未通过**。至少存在以下问题：

- 两个文件都把纯文本直接放入 `<documentation>`，而 2.01 XSD 要求元素内容/XHTML 结构；
- `TankControl.fbd.xml` 还出现 `interface` 位置不符合预期的校验错误。

这暴露的是当前自带样本与标准符合性测试的缺口。本次按要求只报告，不修改样本、读取器或测试。

### 4.4 西门子官方 SimaticML 样本的负向实测

样本来自 Siemens 官方 GitHub 仓库 `siemens/tia-portal-openness-code-snippets`，读取时 `main` 为 `4a8cc79d0666633e524e52f3335d99ff993f8830`；文件路径为 `src/TiaPortal.Openness.CodeSnippets.Plain.Step7/PLC/ProgramBlocks/Resources/PLCBlocks/Block_1`。本次临时副本 SHA-256 为 `0a31c4e78659f48d079daae5013aa59e195e2d3e72a50c250403c779e86ad405`。

样本检查确认：XML 根为 `<Document>`，`<Engineering version="V19"/>`，主对象为 `<SW.Blocks.FB>`，SCL 程序体位于带 Siemens 命名空间的 `<StructuredText>`，由 `<Token>`、`<Access>`、`<NewLine>` 等节点组成；它不是 PLCopen `<project>/<types>/<pous>` 结构。

```bash
xmllint --noout Block_1_V19.xml
PYTHONPATH=src python3 -m plcforge --no-color check Block_1_V19.xml --summary
PYTHONPATH=src python3 -m plcforge --no-color convert Block_1_V19.xml -t python -o -
```

实际结果：`xmllint` well-formed 检查通过；`plcforge check` 与 `convert` 均输出 `PF002: no POUs were parsed from the given sources` 并退出 1。由此可将**这个真实 V19 SimaticML 样本**判定为“通用 XML 可读，但当前 plcforge 不能提取程序或转换”；不能反向推断 SCL 文本路径，也不能把结果外推成所有 SimaticML 版本均已做实测。

样本仓库许可证对 source code/generated source code 给出 MIT 条款，但对其他软件和 object code 另有用途与分发条件。本次只临时读取，不提交样本或 `.zap21` 归档；后续若入库，需先确认具体文件归类并保留相应许可。

### 4.5 施耐德 Control Expert XEF 样本的负向实测

样本来自第三方仓库 `estradege/controlexpert`，固定提交 `ab9e011e8a5bd9aa0aa81f1c53eab13ba00f1600`，路径 `ControlExpert/ControlExpert.XefTests/Stu/m340.xef`，仓库为 MIT License。临时副本 SHA-256 为 `79502e04d39147f2b03f95762789b1b14ff9c6974914f3c0f604e1c138bc4f92`。

样本检查确认：`xmllint` well-formed 检查通过；根元素为 `FEFExchangeFile`，文件头为 `product="Control Expert V15.0 - 201016B"`、`DTDVersion="41"`。这不是 PLCopen `<project>` 结构。

```bash
PYTHONPATH=src python3 -m plcforge --no-color check m340_V15.xef --summary
PYTHONPATH=src python3 -m plcforge --no-color check m340_V15.xef --frontend plcopen --summary
PYTHONPATH=src python3 -m plcforge --no-color convert m340_V15.xef --frontend plcopen -t python -o -
```

实际结果：默认路径为 `PF001`（未知扩展名）加 `PF002`，退出 1；强制 `plcopen` 后，`check` 和 `convert` 仍均为 `PF002`、退出 1。由此可判定当前 plcforge 不能提取或转换这个真实 V15.0 XEF；结果不覆盖 Machine Expert PLCopenXML，也不覆盖其他 XEF 版本。

### 4.6 标称汇川 InoProShop PLCopenXML 工件的部分正向实测

样本来自第三方仓库 `Roarpeng/LMM`，固定提交 `a625078c640483596da6f77ac362f6aedd8d60e5`，文件 `LMM_g_1.3.xml`，SHA-256 为 `98a988f793ba27807e9b72b8a3484912e26b0a82d3ed50370491833c3d0dc248`。仓库未附 LICENSE，因此只临时分析、不提交或再分发样本。

样本约 2.19 MB，well-formed 检查通过；根命名空间为 `http://www.plcopen.org/xml/tc6_0200`，文件头自报 `companyName="Inovance company"`、`productName/productVersion="InoProShop(V1.9.1.6)"`，`contentHeader` 为 `LMM.project`。README 自述面向 AM600 + InoProShop，但本次没有在 InoProShop 中独立执行导入—编译—再导出，故厂商导出身份与机型范围仍以该工件自述为限。

源文件盘点：9 个 POU、9 个 ST body、74 个 `addData`；扩展数据中有 1 个 `globalVars` 列表（439 个变量）和 2 个任务（`MainTask`、`ETHERCAT`）。实际执行结果：

| 检查 | 结果 |
|---|---|
| `plcforge check --summary` | 退出 0；提取 9/9 个 POU，均为 ST；**37 warnings**，其中 `PF210` 17 个、`PF205` 20 个 |
| `plcforge convert -t python` | 退出 0；生成 2,959 行 Python；同样有 37 warnings |
| 生成文件 `py_compile` 与模块导入 | 均通过；9 个 POU 对应顶层符号存在 |
| 源文件与 IR 外部对照 | 9 个 ST POU 被保留，但 IR 的 `globals` 为 0；`addData` 中 439 个全局变量与 2 个任务未进入 IR |
| 行为 | 未执行厂商场景或轨迹对照，**未验证** |

主要警告涉及未知运动控制/总线类型（如 `MC_Power`、`MC_Reset`、`AXIS_REF_SM3`）以及未知例程 `ADR`、`SIZEOF`。因此该样本的正确结论是：**现有 PLCopen 路径能提取并生成这 9 个 ST POU 的 Python，但只构成带依赖缺口和配置丢失的部分转换；不能宣称 AM600 完整工程、任务行为或运动控制语义兼容。** 命名空间是 2.00，本次没有用 2.01 XSD 对它作跨版本符合性结论。

## 5. 分厂商格式与四级结论

以下版本是本次实际查到的官方手册版本，不表示厂商只存在这些版本，也不向未列版本外推。除明确写出“实测”的单元格外，厂商格式能力来自文档，plcforge 能力来自源码；没有真实样本的路径保持未验证。

### 5.1 西门子：TIA Portal / SIMATIC

| 机型、软件与版本 | 文件或导出路径 | 格式核实 | 工程可打开 | 程序可提取 | 代码可转换 | 行为已验证 |
|---|---|---|---|---|---|---|
| TIA Portal V20/V21 项目；具体 CPU 由项目决定 | `.ap20`/`.ap21`；归档 `.zap20`/`.zap21`，旧版为相应版本后缀 | 原生项目/归档，不是 PLCopen XML；归档需在 TIA 中 Retrieve [S1] | **否**，无前端 | **否** | **否** | 未验证 |
| TIA Portal Openness V21；块包括 OB/FB/FC/DB 等，CPU/对象限制按章节 | SimaticML `.xml`；本次实取样为 V19 FB/SCL | Siemens 专用 XML 结构；官方允许导出后离线 XML 处理，但格式随 TIA 版本变化 [S2] | **否，V19 样本实测 0 POU** | **否，实测 `PF002`** | **否，实测退出 1** | 未验证 |
| TIA Portal V21 外部源码；S7-1200/1500 等按语言与块类型限制 | `.scl`；另有 `.stl`、`.db`、`.udt`，Openness STL 使用 `.awl` | 文本源码，不等于 SimaticML；只有 `.scl` 落入现有 ST/SCL 后缀 [S3] | —（非完整工程） | `.scl`：**候选/未验证**；其余后缀无入口 | `.scl`：**候选/未验证**；其余不支持 | 未验证 |
| SIMATIC SD：V20 与 V21 的 S7-1200/1500 等范围不同 | `.s7dcl` 程序文本与 `.s7res` 多语言资源 | 官方明确可脱离 TIA 使用；V20/V21 的语言范围不同，不是 SimaticML [S4] | —（非完整工程） | **否**，当前无入口 | **否** | 未验证 |

补充边界：

- Openness 手册说明压缩归档模式可生成 ZIP-compatible archive，但也有非压缩模式；所以不能凭 `.zapXX` 或 ZIP 文件头推定项目语义可读。本次未找到 `.apXX` 内部项目数据库的公开完整规范，这也不等于官方声明永远无法解析。[S1][S2]
- SimaticML 导出支持的对象有限，跨子树引用可能只成为 open references；未解锁 know-how 保护块只导出公共接口。系统块、安全块、Array/Instance DB 和优化 DB 另有规则，不能把“支持块导出”写成“所有块无损导出”。[S2]
- Openness 文档说明导出版本对应实际使用的 TIA Portal，并警告导出格式为当前版本所用的内部格式、未来可能改变。解析时必须识别 `<Engineering version="…">` 与 Siemens namespace，不能按单一 `.xml` 模式处理所有版本。[S2]
- `.scl` 只是现有通用前端的候选输入。本次没有真实 TIA `.scl` 导出，不能据仓库自制 `conveyor.scl` 的成功结果声称 TIA SCL 已兼容；STL/AWL、DB、UDT 也不是把扩展名改名后就可当作 ST 的格式。
- STEP 7 Classic 未在本次官方资料中完成核实，以上结论不扩展到 Classic。

### 5.2 欧姆龙：Sysmac Studio 与 CX-Programmer

| 机型、软件与版本 | 文件或导出路径 | 格式核实 | 工程可打开 | 程序可提取 | 代码可转换 | 行为已验证 |
|---|---|---|---|---|---|---|
| Sysmac Studio W504-E1-53 / Ver.1.61；NJ/NX/NY 项目 | `.smc`、`.smc2`；compact `.csm`、`.csm2` | 一个文件包含项目数据；`.smc2/.csm2` 为 Ver.1.08+，不是标准 XML；compact 文件还需 build 且有 trace 数据例外 [O1] | **否**，无前端 | **否** | **否** | 未验证 |
| Sysmac Studio IEC 61131-10：官方更新史 Ver.1.30 起确认导入；NX102 unit 1.31+、NX502 1.60、NJ/NX/NY unit 1.19+ | IEC 61131-10 XML **导入**；公开官方通用导出路径未确认 | 官方导入对象是 program/function/FB、data type、global variable，程序为 LD/ST；未据此确认 FBD、任务或完整项目 [O1][O2] | —（交换文件，非原生工程） | **候选/未验证**；没有真实样本 | **候选/未验证** | 未验证 |
| Sysmac Studio Ver.1.61 ST Editor | `Copy` / `Copy with Dependents`；独立 `.st` 文件导出未确认 | 可复制正文，或连同被引用变量和数据类型复制；不是完整项目导出 [O1] | —（非完整工程） | 保存为 `.st` 后是**候选/未验证**；仅正文可能缺接口 | **候选/未验证** | 未验证 |
| Sysmac Studio / Simulink PLC Coder | Simulink PLC Coder 生成的专用 XML | W504 的 `Import ST Program` 指该 XML，不是普通 ST，也不等同 IEC 61131-10 章节 [O1] | **未验证**，无专用前端 | **未验证** | **未验证** | 未验证 |
| CX-Programmer Ver.9.@ / CX-One，W446-E1-23（2023-07） | `.cxp`、密码加密 `.cxps`、文本 `.cxt` | `.cxp` 是通常使用且为 `.cxt` 的压缩形式；`.cxt` 是 text-based，不是 PLCopen XML [O3] | **否**，无前端 | **否**，不能把 `.cxt` 当 ST/XML | **否** | 未验证 |

补充边界：

- 本次公开官方资料只确认 **IEC 61131-10 XML 导入**。第三方 Yellax 称 Ver.1.30 起另有需向 Omron 联系人申请 `activation.exe` 的导出功能，但激活条件、当前版本、对象和 schema 未由公开官方手册确认；该线索既不能写成“官方默认支持导出”，也不能写成“厂商不支持导出”。[O4]
- 官方安装目录 `C:\Program Files\OMRON\Sysmac Studio\Sample\IEC 61131-10 XML\Controller` 含写法示例和用于检查支持数据的 Omron XML schema。该样本/schema 是后续最佳证据来源，但本次没有独立公开下载与再分发许可，故没有实测或入库。[O1]
- `.csm2` 是 compact 项目导出，不应称为控制器备份；控制器 Backup 是另一功能，且不同 Controller 系列的备份文件不兼容。[O1]
- 若取得标准 XML，可立即做通用 XML/XSD 和 plcforge 检查，但 Sysmac 可导入、plcforge 可转换和控制器行为等价仍是三项不同结论。

### 5.3 汇川：AutoShop 与 InoProShop 必须分开

| 机型、软件与版本 | 文件或导出路径 | 格式核实 | 工程可打开 | 程序可提取 | 代码可转换 | 行为已验证 |
|---|---|---|---|---|---|---|
| H5U、Easy / AutoShop A21；版本说明至 V4.12.0.0 | `.hcp`、`.hcpp`、`.updown` 工程；`.hclib` 工程档案 | 官方确认的 AutoShop 工程/依赖打包格式，不是 PLCopenXML；`.hclib` 可含工程、EDS、库和第三方 EtherCAT XML [I1][I2] | **否**，无前端 | **否** | **否** | 未验证 |
| H5U、Easy / AutoShop | `.down`、`.updown`；FB `.fe` | `.updown` 只有生成时勾选工程源码才可上载/再编辑；`.fe` 只有选择“源码可见”才能查看修改，均不是纯 ST [I1] | **否** | **否**，后缀本身不保证有可读源码 | **否** | 未验证 |
| AM、AC / InoProShop C03、V1.10.0.3 | 标准工程 `.project`；升级时在工程目录备份旧工程 | 原生工程；官方工程上载还有 AC700/AC800/AM700 等条件。`.projectarchive` 未由本次官方资料确认 [I4][I5] | **否**，无前端 | **否** | **否** | 未验证 |
| AM、AC / InoProShop；官方历史更新说明 V1.8—V1.9 区间及 V1.10.0.3 包 | PLCOpenXML 导入/导出；实测工件标称 V1.9.1.6、AM600 | 官方确认功能存在并持续修复 LD 导入，但未给出完整 TC6 版本/对象/机型矩阵；实测工件为 `tc6_0200` [I5][I6] | —（交换文件，非原生工程） | **部分实测**：9 个 ST POU；扩展全局变量/任务未保留 | **部分实测**：Python 生成与导入通过，37 警告；非完整工程 | **未验证** |

补充边界：

- AutoShop 手册中的 LiteST 支持不等于独立 `.st` 文件导入/导出；本次未找到该文本出口的官方说明，也未找到 AutoShop 支持 PLCopenXML 的官方证据。手册中的 EtherCAT 设备 XML、Trace XML 和专用工程转换均不能当作 PLCopenXML 证据。[I1][I2]
- InoProShop 官方更新说明明确出现“优化PLCOpenXML功能”“导入导出”等文字，可确认该平台的 XML 路径；这些历史记录不是“首次支持版本”的声明，也不能外推到 AutoShop 或所有 AM/AC 固件。[I5]
- `.projectarchive`、InoProShopTool 控制器备份后缀/容器、AutoShop 原生文件的公开二进制规范均保持未核实。`.down/.updown` 与 `.fe` 是否含源码取决于导出选项，受保护或未含源码的文件不能用于逻辑兼容测试。[I1][I5]
- 第 4.6 节的正向结果只覆盖一份标称 V1.9.1.6 工件：439 个位于 `http://www.3s-software.com/plcopenxml/globalvars` 扩展中的变量和 2 个任务没有进入 IR，运动控制/总线依赖也未解决。因此不能写成“汇川工程已兼容”。
- 未找到明确许可的汇川官方或第三方样本不适合直接入库。`Roarpeng/LMM` 与 `ddvhhm/openFB` 均未发现 LICENSE，只作为本地分析线索；后续应取得作者授权或团队有权再分发的同源工程。[I6][I7]

### 5.4 施耐德：Machine Expert 与 Control Expert 必须分开

| 机型、软件与版本 | 文件或导出路径 | 格式核实 | 工程可打开 | 程序可提取 | 代码可转换 | 行为已验证 |
|---|---|---|---|---|---|---|
| Machine Expert V2.2；M241/M251/M258/M262、LMC058、PacDrive LMC 等 | `.project`、`.projectarchive` | 原生工程与迁移归档；归档可选下载信息、设备描述和库，不等于 PLCopenXML [C3] | **否**，无前端 | **否** | **否** | 未验证 |
| Machine Expert V2.1；V1.2 帮助亦有同类命令 | PLCopenXML `.xml`，导出全部或选定可导出对象 | 官方确认 PLCopen 格式及对象选择限制；不是 `.project` 的简单改名 [C4][C5] | —（交换文件，非原生工程） | **候选/未验证**，无真实样本 | **候选/未验证**；任务等仍受公共 IR 限制 | 未验证 |
| Machine Expert V2.2 | native XML `.export` | CODESYS/Machine Expert native XML 对象导出，和 PLCopenXML 是不同方法 [C3] | **未验证**，无专用前端 | **未验证** | **未验证** | 未验证 |
| Machine Expert Basic V1.4；M221/TM3 | `.smbp`（示例 `.smbe`） | Basic 独立原生工程路径，不能套用标准 Machine Expert [C6] | **否**，无前端 | **否** | **否** | 未验证 |
| Control Expert 16.4；M340/M580、Momentum/Premium/Atrium/Quantum 等 | `.STU` 工作文件、`.STA` 归档 | 原生工作/归档格式；STA 用于版本间 forward compatibility，不是普通 XML [C1][C2] | **否**，无前端 | **否** | **否** | 未验证 |
| Control Expert 16.4 文档；本次实测 V15.0 M340 | `.XEF` Application Exchange XML | 官方为 XML source format；实测根为 `FEFExchangeFile`，不是 PLCopenXML。硬件配置需 `With Conf`，全局 DTM 不在 XEF [C1][C2][C7] | **否，V15 实测 0 POU** | **否，默认/强制均 `PF002`** | **否，实测退出 1** | 未验证 |
| Control Expert；本次资料线索为 V15.0 M340 | `.ZEF` | 含全局 DTM 的导出；一个公开样本为 ZIP，内含 `unitpro.xef` 等，但不能推广到所有版本/加密项目 [C1][C7] | **否**，无工程前端 | **未验证**；解包不等于程序提取 | **否** | 未验证 |

补充边界：

- Machine Expert 的 Native Export 与 PLCOpenXML Export 是两个不同 API/菜单路径。PLCopen 导出会受对象可选范围限制，不可导出对象可报错后继续；官方还给出 `VAR_GLOBAL` 与 `VAR_GLOBAL CONSTANT` 混合列表往返改变的例子。因此生成 XML 文件本身不证明完整无损。[C3][C4][C5]
- Control Expert XEF 官方可包含 entire program、DDT、变量和未保护 DFB 等广泛内容，但不是完整无损备份：硬件配置取决于选项，全局 DTM 需 ZEF，EF/EFB、受保护对象、文档等另有排除。XEF 可读不能跨级成完整工程或行为结论。[C2]
- 本次 XEF 实测样本为 MIT 许可，但只临时分析，未扩大交付范围。另一个 MIT SoMachine `.project` 样本经检查是含 `.meta`/`.object` 等条目的 ZIP 容器，具体 SoMachine 版本未核实；它只能证明该样本可列容器，不证明当前 Machine Expert 原生工程语义可读。[C7][C8]
- Unity Pro 是 Control Expert 13.1 及更早版本的原名；本报告不把 V15/V16 文档结论无差别回推到所有 Unity Pro 版本。[C2]

<!-- VENDOR_SECTIONS_COMPLETE -->

## 6. 完整工程信息的保留边界

下表是源码能够确认的当前公共边界，适用于任何一家厂商的候选导出。某一文件“能转换”时仍需按此表检查丢失项。

| 内容 | 当前公共入口的能力 | 对结论的影响 |
|---|---|---|
| POU 名称、种类、接口、ST/LD/FBD 程序体 | 已实现相应子集；不支持的语法或图元需看诊断 | 只能对实际提取到的 POU 和程序体声称转换 |
| IL/SFC | 保留 POU 接口，程序体不转换 | 不能将接口保留算作完整程序提取或行为验证 |
| STRUCT、ENUM、别名、数组、初值 | ST 与 XML 路径能力不同且均有降级：XML 非 STRUCT 类型无等价节点，ST 别名变成单字段 STRUCT；缺类型默认 BOOL，结构初值未完整读取 | 必须对照导出树与 IR 逐项计数，不能只看 POU 数或退出码 |
| 全局变量与直接地址 | 支持部分 `globalVars` 与变量地址；POU 接口段读取 `constant`/`retain`，配置/资源的同名段属性未完整保留 | 不等于已保留全部变量限定符、I/O 映射和硬件组态 |
| 程序内调用关系 | 调用作为语句/表达式进入 IR | 没有自动生成的工程级调用图；跨任务、实例绑定仍需配置 |
| 配置、资源、任务 | XML 仅收集部分全局变量；ST 配置块跳过 | 周期、优先级、看门狗、程序实例绑定不能视为已转换 |
| 硬件、网络、通信、设备参数 | 没有对应工程模型与厂商前端 | 即使 POU 可转换，也不能声称整机工程可转换 |
| 厂商库与专有功能块 | 没有四家厂商库语义 | 需要库接口/实现及目标运行时语义，不能只保留同名调用就认定等价 |
| 加密、Know-how 保护、无源程序块 | 没有解密或恢复能力；缺程序体时最多保留已导出的接口 | 受保护内容必须单列为不可验证，不能计入成功转换比例 |

## 7. 样本来源、许可与后续测试集

### 7.1 本次样本盘点

- 仓库现有 `examples/` 是 plcforge 自制示例；没有四家厂商原生工程或由厂商软件产生的导出文件。
- 本次额外实测三类外部工件：西门子官方 V19 `Block_1` SimaticML、MIT 许可的 Control Expert V15.0 `m340.xef`，以及第三方公开但未附 LICENSE、文件头标称 InoProShop V1.9.1.6 的 `LMM_g_1.3.xml`，详见第 4.4—4.6 节。它们分别只代表所述格式与版本，不能代表对应厂商全部平台。欧姆龙仍无符合要求的真实导出样本。
- 本次没有把手册中的截图、代码片段或人工重写片段当作厂商样本。
- 本次只临时下载 PLCopen 官方 XSD 做校验，未把标准文件或第三方样本复制进仓库。
- 任何厂商结论都不能由通用样例结果升级。外部工件只提升其对应样本的实测等级：汇川样本是带信息丢失和警告的部分转换，西门子 SimaticML 与施耐德 XEF 是现有路径不支持；四家行为均未验证。

“公开可下载”不自动等于“允许再分发”。后续样本入库前必须分别记录下载 URL、作者/发布者、软件版本、导出时间、许可或书面授权、SHA-256；厂商手册的版权许可也不能替代样本许可。

### 7.2 每家厂商应取得的最小证据包

每个机型/软件版本组合至少保留以下一组同源文件，而不是从不同项目拼合：

1. 原生工程及其归档；
2. 由明确版本工程软件生成的文本源码和 XML 导出，记录菜单、导出选项与软件 build；
3. 一个无保护的小型 ST POU，一个 LD 或 FBD POU，以及它们引用的类型和变量；
4. 一个包含多 POU 调用、用户类型、数组/字符串、标准定时器、全局变量和任务配置的较完整工程；
5. 导出清单：原文件中 POU/类型/全局变量/任务数量、缺失与受保护对象清单；
6. 若后续要声称行为等价，再补同一输入序列下由厂商软件或控制器产生的输出/状态轨迹。

建议按以下用途分组，而不是把一种格式当作一家厂商的代表：

| 厂商 | 小型离线解析候选 | 完整工程/行为测试必须补充 |
|---|---|---|
| 西门子 | 同一未保护块的 SCL 与 SimaticML 各一份，注明 TIA 与 CPU 版本 | OB/FB/FC、实例 DB、PLC 数据类型、调用关系、循环/事件任务；SimaticML 与 SCL 结果交叉核对 |
| 欧姆龙 | Sysmac Studio 实际 XML/ST 导出及原生项目，注明 NJ/NX/NY 等机型 | 全局/结构/数组、功能块实例、任务与程序绑定；若覆盖 CJ/CP，则另取 CX-One 样本，不与 Sysmac 混用 |
| 汇川 | AutoShop 与 InoProShop 分开取样，记录具体机型和版本 | 各软件对应的任务、库、数据类型与硬件配置；不能用 CODESYS 示例代替汇川导出 |
| 施耐德 | Machine Expert PLCopenXML 与 Control Expert XEF 分开取样 | Machine Expert 的 CODESYS 项目配置、Control Expert 的段/任务/数据与硬件；两类软件不合并结论 |

### 7.3 受保护和缺失内容的处理

- 对没有源程序体、加密或导出时被省略的块，结果必须标为“接口/元数据可见，程序未提取”。
- 对缺少用户库或类型的工程，保留诊断并区分“前端无法读取”和“链接/类型解析缺依赖”。
- 对只有接口而无行为的 POU，不执行行为通过率统计；对只有单 POU 文本的样本，不作完整工程结论。

## 8. 当前无需厂商软件可继续做什么

| 工作 | 当前可否执行 | 结论边界 |
|---|---|---|
| 盘点真实文件的扩展名、文件头、容器目录、XML 根元素和命名空间 | 可以，取得文件后仅需通用工具 | 只能证明容器/文本结构，不证明工程语义可读 |
| 对声称 PLCopen XML 的文件做 well-formed 与相应 XSD 校验 | 可以 | XSD 通过仍不代表 plcforge 支持其中全部对象；XSD 失败需与厂商扩展分开分析 |
| 对真实 SCL/ST、PLCopen XML 执行 `plcforge check` 和 `convert` | 可以 | 可升级为实际样本转换结论，必须同时保留诊断与未转换对象 |
| 统计导出前后的 POU、变量、类型、调用与任务 | 可以，但必须用源文件与 IR/输出的外部对照，任务等缺失项需要单独脚本或人工清单 | 现有诊断会漏报部分跳过/降级，不能只用“退出码为 0”或诊断数判断完整性 |
| 编译并运行生成的 Python/C++，执行人工定义的测试向量 | 可以 | 只能验证所定义场景；没有厂商轨迹时不能声称厂商行为等价 |
| 直接打开四家原生工程并生成厂商导出 | 当前不可以 | 需要相应厂商软件/版本，或之后另经验证的专用读取器 |
| 验证专有功能块、任务调度、硬件 I/O 与控制器完全一致 | 当前不可以 | 需要厂商语义资料、库实现及厂商环境/权威轨迹 |

## 9. 建议的后续顺序（不在本任务内实施）

1. **先修复实测基线，再比较厂商。** 单独处理自带 PLCopen XML 未通过官方 XSD 校验的问题，并增加根元素/格式识别与标准校验测试；否则自制样本会持续造成“已兼容”的错觉。
2. **按第 7 节取得同源、许可清楚的厂商样本。** 优先每家一个未保护小工程，再取大型真实工程；先冻结版本与哈希，再执行转换。
3. **先走已有入口，不为原生工程直接开发大解析器。** SCL/ST、标准 PLCopen XML 先做样本诊断和保留率统计；只有证据表明出口稳定且确有必要时，再评估 SimaticML、XEF 或厂商特定 PLCopen 扩展适配。
4. **汇川先补官方格式证据。** AutoShop/InoProShop 与不同机型不可由 CODESYS 一般能力替代核实。
5. **行为验证最后升级。** 先验证生成代码可运行，再用同一输入轨迹对照厂商软件/控制器；任务、硬件和专有库必须单列，不能混入单 POU 算法等价结论。

以上均是证据与下一步建议，本任务没有新增厂商解析器或改变产品逻辑。

## 10. 官方资料与可追溯来源

以下来源均在 2026-10-28 读取。网页内容可能更新，结论以本次记录的版本/章节为限；没有把第三方博客、论坛或仅凭文件后缀的推断作为官方结论。

### 10.1 标准组织

- **[P1] PLCopen，XML Exchange。** 说明 TC6 XML 用于在不同工具间交换文本和图形信息；页面标明最新规范版本为 2.01，并说明 PLCopen XML 于 2019 年成为 IEC 61131-10。  
  https://www.plcopen.org/standards/xml-echange/
- **[P2] PLCopen，Downloads。** 本次实际下载条目 `PLCopen XML version 2.01 xsd file`；同页也提供 `PLCopen XML technical document version 2.01.pdf` 与 IEC 61131-10 Code Components。  
  https://www.plcopen.org/downloads/
- **[P3] PLCopen，Code Components of IEC 61131-10。** 说明 IEC 标准 Code Components 的下载与许可提示；不能据此推定任何厂商样本可再分发。  
  https://www.plcopen.org/standards/xml-echange/code-components/

### 10.2 西门子

- **[S1] Siemens，《Editing projects》V21（11/2025）与 V20（11/2024）。** 项目/参考项目扩展名、归档与 Retrieve；另以 Openness V21 的 `Archiving and Retrieving a project` 核实压缩模式。  
  https://docs.tia.siemens.cloud/r/en-us/v21/editing-projects/using-reference-projects/opening-and-closing-a-reference-project  
  https://docs.tia.siemens.cloud/r/en-us/v21/editing-projects/archiving-and-retrieving-projects/working-with-project-archives  
  https://docs.tia.siemens.cloud/r/en-us/v20/editing-projects/using-reference-projects/opening-and-closing-a-reference-project  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/tia-portal-openness-api/functions-for-projects-and-project-data/archiving-and-retrieving-a-project
- **[S2] Siemens，《TIA Portal Openness: API for automation of engineering workflows》V21（03/2026）。** `Basic principles of importing/exporting`、`Field of application for Import/Export`、`Version Specific Simatic ML Import`、`Exporting blocks`、`Exporting blocks with know-how protection`、`Changes of the object model and XML file format`。  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/overview/basic-principles-of-importing/exporting  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/overview/field-of-application-for-import/export  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/overview/version-specific-simatic-ml-import  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/importing/exporting-data-of-a-plc-device/blocks/exporting-blocks  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/importing/exporting-data-of-a-plc-device/blocks/exporting-blocks-with-know-how-protection  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/importing/exporting-data-of-a-plc-device/blocks/changes-of-the-object-model-and-xml-file-format
- **[S3] Siemens，《Creating and managing blocks》V21（11/2025），`Saving blocks as external source files`；Openness V21，`Generate source from block`。**  
  https://docs.tia.siemens.cloud/r/en-us/v21/creating-and-managing-blocks/using-external-source-files-for-stl-and-scl/saving-blocks-as-external-source-files  
  https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/tia-portal-openness-api/functions-for-accessing-the-data-of-a-plc-device/blocks/generate-source-from-block
- **[S4] Siemens，《Creating and managing blocks》，`Exporting and importing blocks in SIMATIC SD format`，V21（11/2025）与 V20（11/2024）。**  
  https://docs.tia.siemens.cloud/r/en-us/v21/creating-and-managing-blocks/exporting-and-importing-blocks-in-simatic-sd-format-s7-1200-s7-1500-s7-1200-g2/exporting-and-importing-blocks-in-simatic-sd-format-s7-1200-s7-1500-s7-1200-g2  
  https://docs.tia.siemens.cloud/r/en-us/v20/creating-and-managing-blocks/exporting-and-importing-blocks-in-simatic-sd-format-s7-1200-s7-1500/exporting-and-importing-blocks-in-simatic-sd-format-s7-1200-s7-1500
- **[S5] Siemens，`tia-portal-openness-code-snippets` 官方仓库、V19 `Block_1` 与许可证。** 本次读取的仓库提交为 `4a8cc79d0666633e524e52f3335d99ff993f8830`。  
  https://github.com/siemens/tia-portal-openness-code-snippets  
  https://github.com/siemens/tia-portal-openness-code-snippets/blob/4a8cc79d0666633e524e52f3335d99ff993f8830/src/TiaPortal.Openness.CodeSnippets.Plain.Step7/PLC/ProgramBlocks/Resources/PLCBlocks/Block_1  
  https://github.com/siemens/tia-portal-openness-code-snippets/blob/4a8cc79d0666633e524e52f3335d99ff993f8830/LICENSE.md

### 10.3 欧姆龙

- **[O1] OMRON，《Automation Software Sysmac Studio Version 1 Operation Manual》W504-E1-53，2025-01，对应 Ver.1.61。** §3-3-7（印刷页 3-20 / PDF 92）核实 `.smc/.smc2/.csm/.csm2`；§4-6-2（4-156 / PDF 342）核实 ST 复制；§8-20（8-144 / PDF 822）区分 Simulink XML；§8-21（8-146 / PDF 824）核实 IEC 61131-10 导入对象、样本/schema 路径和 CPU 条件。临时核对文件 SHA-256：`a9a7bcd19f4af04dc23eabb129c8297249a976a0fe805094fe5a2235cae83f21`。  
  https://assets.omron.eu/downloads/latest/manual/en/w504_sysmac_studio_operation_manual_en.pdf
- **[O2] OMRON，Sysmac Studio Update Information。** Ver.1.08 条目确认 `smc2/csm2`，Ver.1.30 条目只写 `Support the import function of IEC61131-10XML`；本次页面覆盖至 Ver.1.68（2026-10）。  
  https://www.fa.omron.co.jp/product/tool/454/sysmac-studio/e1_doc.html
- **[O3] OMRON，《SYSMAC CX-Programmer Ver. 9.@ / CXONE-AL@@D-V4 Operation Manual》W446-E1-23，2023-07。** Part 1 Chapter 2（印刷页 10 / PDF 58）及 Chapter 3（印刷页 52 / PDF 100）核实 `.CXP/.CXT/.CXPS`。临时核对文件 SHA-256：`e9fe649453771f206721b845147ee4def9ca2b91d4c650526cb577bccef2770a`。  
  https://assets.omron.eu/downloads/latest/manual/en/w446_cx-programmer_operation_manual_en.pdf
- **[O4] 非官方线索：Yellax，`Sysmac Studio nowadays supports PLCopen XML import/export`，2020-05-07。** 文中声称 Ver.1.30+ 导出需向 Omron 联系人申请激活程序；只用于提出待核实路径，不作为 Omron 官方兼容承诺。  
  https://www.yellax.com/en/nieuws/sysmac-studio-nowadays-supports-plcopen-xml-import-export/

### 10.4 汇川

- **[I1] 汇川，《H5U&Easy系列可编程逻辑控制器编程手册》19011157-SC A21，2026-09。** §5.4.2、§5.4.7、§5.10、§6.11.1、§26.1、§29.3；PDF 物理页 80、83、95、210、899、1011—1023。  
  https://www.inovance.com/portal/serviceSupport/download?key=H5U  
  https://newweb.inovance.com/owfile/ProdDoc/SC/19011157-SC/A21/19011157-SC_A21_H5U%26Easy%E7%B3%BB%E5%88%97%E5%8F%AF%E7%BC%96%E7%A8%8B%E9%80%BB%E8%BE%91%E6%8E%A7%E5%88%B6%E5%99%A8%E7%BC%96%E7%A8%8B%E6%89%8B%E5%86%8C_CN_20260904.pdf
- **[I2] 汇川，《AutoShop V4.12.0.0版本说明书》19012791-SC A02，2026-06。** LiteST 版本记录与 EtherCAT XML 范围；不能改称其他补丁版本的说明书。  
  https://newweb.inovance.com/owfile/ProdDoc/SC/19012791-SC/A02/19012791-SC_A02_AutoShop%20V4.12.0.0%E7%89%88%E6%9C%AC%E8%AF%B4%E6%98%8E%E4%B9%A6_CN_20260616.pdf
- **[I4] 汇川，《中型PLC编程手册（软件使用篇）》PS00003145_PDF_CN C03，2026-08；InoProShop V1.10.0.3。** §3.14 工程上载、§4.2.8 `.project`、§4.4.2 EtherCAT XML。  
  https://newweb.inovance.com/owfile/ProdDoc/SC/PS00003145_PDF_CN/C03/%E4%B8%AD%E5%9E%8BPLC%E7%BC%96%E7%A8%8B%E6%89%8B%E5%86%8C%EF%BC%88%E8%BD%AF%E4%BB%B6%E4%BD%BF%E7%94%A8%E7%AF%87%EF%BC%89-CN-C03.PDF
- **[I5] 汇川，InoProShop V1.10.0.3 官方完整包内《InoProShop(V1.10.0.3)更新说明》，2026-08。** PDF 物理页 10、12—17、24、28：PLCOpenXML 导入导出、LD 导入优化、工程升级备份和控制器备份条件。包内嵌套路径为 `软件升级说明文档/InoProShop(V1.10.0.3)更新说明.pdf`。  
  https://www.inovance.com/portal-front/api/support/doc/search?key=InoProShop&showChannel=2  
  https://newweb.inovance.com/owfile/upload/docInfo/2026/08/14/7bd0c718-aa9b-4c90-ab51-59291c6df7c9.zip
- **[I6] 第三方工件：Roarpeng/LMM。** 固定提交 `a625078c640483596da6f77ac362f6aedd8d60e5`；文件头标称 InoProShop V1.9.1.6，README 自述 AM600。未发现 LICENSE，非汇川官方支持证明。  
  https://github.com/Roarpeng/LMM/tree/a625078c640483596da6f77ac362f6aedd8d60e5  
  https://raw.githubusercontent.com/Roarpeng/LMM/a625078c640483596da6f77ac362f6aedd8d60e5/LMM_g_1.3.xml
- **[I7] 第三方工件：ddvhhm/openFB。** AutoShop 原生工程和 FB 线索；未发现 LICENSE，不作为格式规范或再分发样本。  
  https://github.com/ddvhhm/openFB

<!-- OTHER_VENDOR_REFERENCES -->

### 10.6 本地源码与实测依据

- 当前提交中的 `src/plcforge/pipeline.py`、`src/plcforge/frontends/plcopen/reader.py`、`src/plcforge/frontends/st/parser.py`、`src/plcforge/ir/nodes.py`，具体行号见第 3 节。
- 当前提交中的 `examples/` 与 `tests/`；第 4 节命令和结果均在本任务环境实际执行。
- PLCopen 2.01 XSD 的临时校验文件与 SHA-256 见第 4.3 节；未提交到仓库。
