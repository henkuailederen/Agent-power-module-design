## Power Module Agent：功率模块结构设计 + CAD + 仿真 + 优化智能体

> **LLM + Tool Pool** 驱动的功率模块结构设计(工具易扩展)：**代码编程式 CAD（STEP）** + **MATLAB–COMSOL 联合仿真（.mph）** + **数据后处理（.m or.py）** + **优化（.py）**

语言：**中文** | [English README](README.en.md)

面向功率模块封装/结构设计的对话式 Agent：以 **JSON 结构参数**驱动 **CadQuery 编程式建模**（导出 STEP），并可通过 **MATLAB Engine + COMSOL LiveLink** 完成仿真，支持模拟退火（SA）的优化算法（可拓展）。

> 适用场景：快速生成封装方案、批量参数扫描、自动化热仿真评估、优化芯片布局/关键尺寸、把“设计—仿真—优化”从手工流程变成可拓展的Agent流。

---

### 目录（Table of Contents）

- [主要能力（Features）](#主要能力features)
- [整体架构（Architecture）](#整体架构architecture)
- [环境依赖（Prerequisites）](#环境依赖prerequisites)
- [安装（Installation）](#安装installation)
- [配置（Configuration）](#配置configuration)
- [快速开始（Quick Start）](#快速开始quick-start)
- [如何扩展 Tool Pool（Extensibility）](#如何扩展-tool-poolextensibility)
- [工具接口标准化设计指南（Tool Contract）](#工具接口标准化设计指南tool-contract)
- [目录结构（Project Layout）](#目录结构project-layout)
- [常见问题（FAQ / Troubleshooting）](#常见问题faq--troubleshooting)
- [贡献（Contributing）](#贡献contributing)
- [许可证（License）](#许可证license)
- [致谢（Acknowledgements）](#致谢acknowledgements)

---

### 主要能力（Features）

- **工具库**：把“参数 → CAD → 仿真 → 后处理 → 指标 → 迭代/优化”串起来，根据用户意图完成任务。
- **强校验 + 预检**：
  - Pydantic 结构校验：在进入 CAD 构建前就把字段类型/结构问题挡住（`src/tools/cad_schema.py`）。
  - 几何预检：在渲染/导出前检查芯片越界与重叠，并给出可操作的修复建议（`src/tools/json_precheck.py`）。
- **JSON → STEP 的 CAD 流水线**：自动渲染 CAD Python 脚本并导出 STEP（输出到 `data/step/`），同时沉淀可复现的输入/中间件：`data/json/` 与 `data/py/`。
- **MATLAB–COMSOL 联合仿真自动化**：
  - STEP → COMSOL 求解 → 保存 `.mph`（`run_thermal_sim_from_step`）。
- **可复现的产物管理**：
  - 用 `module_id` 统一标识一个结构版本（对应 `data/json/<module_id>.json`、`data/step/<module_id>.step`）。
  - 用 `run_id` 统一标识一个仿真 case（同一 `run_id` 覆盖 `.mph`，CSV 追加记录历史对比）。
- **优化框架（可插拔）**：提供算法无关的 `opt_*` 会话接口（当前已实现 SA 数学内核），你可以把“候选设计评估”接到真实仿真或历史数据上。
- **Tool Pool 易扩展**：你可以把任何可程序化的能力（CAD、仿真、后处理、数据库检索、规则检查、可视化等）封装为工具加入池子，让 LLM 自动编排。
- **对话式多轮工具编排**：工作流支持多轮 tool-calling 循环，能在一个回答中连续调用多个工具直至收敛（`src/agent/workflows.py`）。

---

### 整体架构（Architecture）

本项目刻意把“工程能力”与“智能体编排”解耦，保证可维护性与可扩展性：

- **底层能力层（可复用、可单测）**：`src/tools/`
  - CAD：`template_merge.py`（模板+覆盖）、`cad_build.py`（导 STEP）、`json_precheck.py`（几何预检）
  - 仿真：`matlab_session.py`（MATLAB Engine + COMSOL 启动/挂接）、`sim_tools.py`（仿真与后处理封装）
  - 优化：`opt/`（当前 SA 内核）
- **Tool Pool（对 LLM 暴露的工具表）**：`src/agent/tools.py`
  - 使用 `@tool` 把底层函数封装为 LangChain 工具
  - 在 `get_all_tools()` 汇总注册
- **工作流/编排层（工具调用循环）**：`src/agent/workflows.py`
  - `llm.bind_tools(get_all_tools())` 绑定工具
  - 执行“LLM → tool_calls → 执行工具 → ToolMessage 回传 → 再调用 LLM”的循环


---

### 环境依赖（Prerequisites）

本项目分为两条链路：**纯 Python 的 CAD/JSON 处理**，以及需要商业软件的 **MATLAB-COMSOL 联合仿真**。

#### 1) Python 环境

- **Python**：建议 `3.9+`
- **依赖安装**：见 `requirements.txt`
- **CadQuery**：用于参数化建模与 STEP 导出（`cadquery>=2.5`）

> CadQuery 在 Windows 下有时更推荐用 Conda 安装（避免 OCC/依赖问题）；但本项目也提供了 pip 依赖清单，按你的环境选择即可。

#### 2) 联合仿真环境（可选，但启用仿真必须）

- **MATLAB**（需安装并可运行）
- **MATLAB Engine API for Python**（必须安装到你的 Python 环境中）
- **COMSOL Multiphysics**
- **LiveLink™ for MATLAB®**（COMSOL 与 MATLAB 的接口）

本项目会在 Python 侧启动 MATLAB Engine，并调用 `mphstart` 连接 COMSOL（必要时会尝试启动本地 `comsolmphserver`）。

---

### 安装（Installation）

#### 1) 克隆仓库

- `git clone <YOUR_REPO_URL>`
- `cd Power-Module-Agent`

#### 2) 创建虚拟环境并安装依赖

Windows PowerShell：

- `python -m venv .venv`
- `.venv\Scripts\Activate.ps1`
- `pip install -r requirements.txt`

#### 3)（可选）安装 MATLAB Engine for Python

在 MATLAB 安装目录下执行（示例路径需要你按版本修改）：

- `cd "C:\Program Files\MATLAB\R2023b\extern\engines\python"`
- `python -m pip install .`

#### 4)（可选）配置 COMSOL LiveLink 路径/端口

默认按 COMSOL 6.2 的 Windows 安装路径查找；如果你的安装路径/版本不同，建议通过环境变量覆盖：

- `COMSOL_MLI_DIR`：例如 `C:\Program Files\COMSOL\COMSOL62\Multiphysics\mli`
- `COMSOL_BIN_DIR`：例如 `C:\Program Files\COMSOL\COMSOL62\Multiphysics\bin\win64`
- `COMSOL_PORT`：默认 `2036`

这些变量由 `src/tools/matlab_session.py` 读取。

---

### 配置（Configuration）

#### 1) 模型服务（LLM）

在项目根目录创建 `.env`（不要提交到 Git）。

你可以先复制 `env.example` 为 `.env`，再按需修改：
- `copy env.example .env`（Windows PowerShell 可用 `Copy-Item env.example .env`）

- `OPENAI_API_KEY=你的_API_Key`
- `OPENAI_BASE_URL=https://api.deepseek.com`
- `OPENAI_MODEL=deepseek-chat`

> 兼容 OpenAI 协议的服务也可以使用：只需要替换 `OPENAI_BASE_URL` 与 `OPENAI_MODEL`。

---

### 快速开始（Quick Start）

#### 1) 启动对话式 Agent（CLI）

- 运行：`python -m src.main`

启动后你可以让智能体：

- 基于模板生成结构 JSON
- 生成 STEP（CadQuery）
- 运行热仿真并输出芯片最高温 CSV（MATLAB + COMSOL）

输入 `exit` / `quit` 退出。

#### 2) 不通过大模型，直接生成 STEP（建议的最小流程）

你可以通过智能体调用工具完成（推荐），或直接在 Python 层调用 `src/tools/template_merge.py` 与 `src/tools/cad_build.py` 里的底层函数完成：

- **输入**：选择模板（如 `3P6P_V1`）并给出少量 overrides（务必提供 `module_id`）
- **输出**：
  - `data/json/<module_id>.json`
  - `data/py/<module_id>_cad.py`
  - `data/step/<module_id>.step`

#### 3)（可选）从 STEP 运行热仿真并统计芯片最高温

仿真链路对应的工具封装位于 `src/tools/sim_tools.py`，核心流程是：

- **STEP → 求解并保存 .mph**：工具 `run_thermal_sim_from_step`
- **.mph → 芯片最高温统计**：工具 `compute_chip_maxT_from_mph`

说明：
- `.mph` 输出位于 `data/sim_results/`，同一 `run_id` 会覆盖对应的 `<run_id>_thermal.mph`
- 芯片最高温 CSV 会写入 `*_chip_maxT.csv`，并以“追加”模式累积记录

---

### 如何扩展 Tool Pool（Extensibility）

你可以把任何“可自动化的工程动作”做成工具加入 tool pool，让智能体具备新的能力。当前 tool pool 的注册入口是 `src/agent/tools.py`，工作流会从 `get_all_tools()` 读取并绑定到 LLM（见 `src/agent/workflows.py`）。

#### A) Tool Pool 的扩展原则（推荐）

- **底层函数放在 `src/tools/`**：尽量写成纯函数/轻副作用函数（输入输出清晰，可独立调用与调试）。
- **对 LLM 友好的返回值**：
  - 成功/失败用结构化字段表达（例如 `{"success": True/False, ...}`）
  - 返回内容尽量是 JSON 可序列化的基础类型（str/float/int/list/dict）
  - 大产物（STEP、.mph、CSV）用“路径”返回，并写入 `data/` 以便复现
- **在 Tool wrapper 中做“人类可读报告”**：LLM 更擅长读摘要，而不是读 raw dict。
- **工具要稳定可重复**：相同输入尽量得到相同输出；需要随机性时明确 `run_id/module_id/seed`。

#### B) 新增一个工具（最常见）

1) **在 `src/tools/` 新增底层能力**：函数式接口，输入输出清晰，返回值建议为结构化 dict（见下方“工具接口标准化设计指南”）。

2) **在 `src/agent/tools.py` 新增一个 Tool wrapper**：使用 `@tool("<tool_name>")` 把底层能力对 LLM 暴露，wrapper 负责把结构化结果转成“人类可读 + 关键路径/指标清晰”的文本。

3) **注册到 tool pool**：在 `src/agent/tools.py` 的 `get_all_tools()` 里追加该工具。

4)（推荐）**更新提示词规范**：在 `src/agent/prompts.py` 里说明“何时调用该工具 / 输入字段约定 / 出错时如何自修复或向用户追问”。

#### C) 新增 MATLAB/COMSOL 侧能力（仿真或后处理）

推荐流程：

- **在 `sim/` 写好 `.m` 函数**（函数式接口、参数明确、尽量返回基础数据或路径）
- **在 `src/tools/sim_tools.py` 增加 Python 封装**：通过 `get_matlab_engine()` 调用 `.m`，并把结果转为 Python 基础类型
- **最后在 `src/agent/tools.py` 暴露为工具**（同上）

你可以用 `python -m src.debug_matlab_link` 先验证 MATLAB–COMSOL 链路是否可用。

#### D) 新增优化算法（可选）

当前优化暴露的是算法无关的 `opt_*` 接口，但数学内核仅实现了 SA（`opt/sa`）。如果你要扩展到 PSO/BO/GA 等，建议：

- 在 `opt/<algo>/` 实现同样的会话 API（create/get_state/propose/step）
- 在 `src/agent/tools.py` 里新增相应 wrapper，并在 `get_all_tools()` 注册

---

### 工具接口标准化设计指南（Tool Contract）

为了让 tool pool **可持续扩展、可复现、可维护**，建议所有工具遵循统一的“接口契约”。本项目当前已经采用了“safe wrapper + 结构化结果”的风格（例如 `src/tools/cad_build.py`、`src/tools/sim_tools.py`），下面是推荐的标准化规范。

#### 1) 工具命名与职责边界

- **命名稳定**：工具名一旦发布尽量不改（兼容性），建议使用动词开头：`build_*`、`run_*`、`compute_*`、`export_*`、`validate_*`、`list_*`。
- **单一职责**：一个工具只做一类事情（构建 / 求解 / 后处理 / 校验 / 列表），避免“巨无霸工具”难以测试和复用。
- **可组合**：优先把“长链路”拆成多个小工具，让工作流去编排（本项目的仿真链路即 STEP→.mph 与 .mph→统计 分离）。

#### 2) 输入接口（Input Contract）

- **输入必须可 JSON 序列化**：只使用 `str/int/float/bool/list/dict` 等基础类型（便于 LLM 与日志系统处理）。
- **字段要写清单位与默认值**：例如 `h` 的单位、`P_igbt/P_fwd` 的单位、`dataset` 的默认值等。
- **ID 规范**：
  - **`module_id`**：标识一个结构版本；同名应覆盖生成 `data/json/<module_id>.json` 与 `data/step/<module_id>.step`（便于迭代）。
  - **`run_id`**：标识一个仿真 case；同名应覆盖对应 `.mph`，但允许结果 CSV 追加记录（便于对比历史）。
- **输入校验前置**：
  - 结构/类型校验：建议用 Pydantic（示例：`src/tools/cad_schema.py`）。
  - 几何/物理预检：把“明显不可能成功”的 case 在早期挡住（示例：`src/tools/json_precheck.py`）。

#### 3) 输出接口（Output Contract）

- **统一返回结构化结果**（推荐所有底层能力函数都返回 dict）：
  - `success: bool`
  - 成功时包含关键字段（例如 `step_path`、`model_path`、`csv_path`、`chips` 等）
  - 失败时至少包含：`error`（人类可读），可选 `traceback`/`matlab_output` 等诊断信息
- **产物落盘规范**：
  - 统一写入 `data/` 目录下可预期的位置（例如 `data/step/`、`data/sim_results/`）
  - 输出中返回路径（字符串），避免直接返回二进制/大体积内容
- **对 LLM 友好**：
  - Tool wrapper（`src/agent/tools.py`）建议返回“摘要 + 路径 + 下一步建议”，而不是把超长原始数据直接刷屏。

#### 4) 幂等性与可复现（Idempotency & Reproducibility）

- **同一输入可重复运行**：相同参数应产生相同的关键输出（或至少输出路径一致、可追踪）。
- **覆盖/追加策略要固定且写入文档**：
  - CAD：同 `module_id` 覆盖 JSON/STEP
  - 仿真：同 `run_id` 覆盖 `.mph`，CSV 追加
- **不要隐式读写随机状态**：需要随机性时应显式暴露 `seed`。

#### 5) 错误分类与可恢复性（Error Handling）

- **错误要可行动**：错误信息最好包含“怎么改”（例如缺字段、类型不对、越界/重叠建议位移、路径缺失、依赖未安装）。
- **把“环境错误”与“输入错误”区分开**：
  - 输入错误：应提示修改参数/JSON
  - 环境错误：应提示安装/配置（例如 MATLAB Engine、COMSOL LiveLink、路径变量）
- **长链路拆分**：把“求解失败”和“后处理失败”拆成不同工具，避免一次失败丢失全部上下文。

#### 6) 观测性（Logging & Telemetry）

- 关键事件建议打日志：工具名、关键参数（脱敏）、产物路径、耗时、异常摘要。
- 推荐在日志里带上 `module_id/run_id/session_id` 等“关联 ID”，便于复盘一条链路。

---

### 目录结构（Project Layout）

- `cad/`：CAD 模板与渲染工具（Jinja2 模板、参考配置等）
  - `cad/reference/`：参考模板（注意：扩展名为 `.json`，但内容为 YAML）
  - `cad/template.j2`：CAD 脚本模板
- `data/`：所有可复现产物与中间结果
  - `data/json/`：器件 JSON（以 `module_id` 命名）
  - `data/py/`：渲染得到的 CAD Python 脚本
  - `data/step/`：导出的 STEP 3D 模型
  - `data/sim_results/`：`.mph` 与后处理 CSV
  - `data/opt/`：优化历史/元信息
- `sim/`：MATLAB 脚本（`run_sim_from_step.m`、`compute_chip_maxT.m` 等）
- `src/`：Python 主工程
  - `src/main.py`：CLI 入口
  - `src/config.py`：LLM 配置（从 `.env` / 环境变量读取）
  - `src/agent/`：智能体层（提示词/工作流/工具封装）
  - `src/tools/`：CAD / 仿真 / 优化底层工具

---

### 常见问题（FAQ / Troubleshooting）

#### 1) 无法导入 `matlab.engine`

请先安装 MATLAB Engine for Python（见上方安装步骤），然后运行：

- `python -m src.debug_matlab_link`

#### 2) COMSOL 连接失败 / `mphstart` 报错

- 确认已安装 **LiveLink™ for MATLAB®**
- 检查 `COMSOL_MLI_DIR` / `COMSOL_BIN_DIR` 是否正确
- 检查 `COMSOL_PORT` 端口是否被占用

#### 3) CadQuery 安装失败

不同平台对 OCC 依赖较敏感；建议优先用 Conda 创建环境，或查阅 CadQuery 官方安装说明后再安装本项目依赖。

---

### 贡献（Contributing）

欢迎提交 Issue / PR：

- 新增模板与约束：`cad/reference/`、`src/tools/cad_schema.py`
- 新增仿真/后处理脚本：`sim/` 并在 `src/tools/sim_tools.py` 增加 Python 封装
- 新增工具并暴露给 Agent：`src/agent/tools.py`（并在 `get_all_tools()` 注册）
- 新增/调整工作流：`src/agent/workflows.py`（例如增加“规划 → 工具链执行 → 总结”多阶段流程）

贡献细则请阅读：`CONTRIBUTING.md`

---

### 许可证（License）

本项目使用 **MIT License**，详见根目录 `LICENSE` 文件。

---

### 致谢（Acknowledgements）

- CadQuery（参数化 CAD / STEP 导出）
- LangChain（工具调用与 Agent 框架）
- MATLAB Engine API for Python
- COMSOL Multiphysics & LiveLink for MATLAB
