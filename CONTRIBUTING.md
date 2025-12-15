## 贡献指南（Contributing）

感谢你愿意为 **Power Module Agent** 做贡献！本项目的核心目标是：把功率模块结构设计中的 **CAD（STEP）/ 联合仿真（MATLAB–COMSOL）/ 后处理 / 优化** 做成可复现、可组合、可扩展的 Tool Pool。

语言：**中文** | [English Contributing Guide](CONTRIBUTING.en.md)

---

### 开发环境建议

- **Python**：建议 3.9+
- **依赖安装**：`pip install -r requirements.txt`
- **可选（仿真链路）**：
  - MATLAB
  - MATLAB Engine API for Python
  - COMSOL Multiphysics + LiveLink™ for MATLAB®

配置文件：
- 复制 `env.example` 为 `.env`，按需填写变量（不要提交 `.env`）

---

### 代码结构与扩展点

- **底层能力层（可复用）**：`src/tools/`
- **Tool Pool 注册**：`src/agent/tools.py` 的 `get_all_tools()`
- **工具编排工作流**：`src/agent/workflows.py`
- **行为规范/调用约定**：`src/agent/prompts.py`

---

### 如何新增一个 Tool（推荐流程）

目标：新增一个工具并加入 tool pool，让智能体可以自动调用。

#### 1) 先写底层能力（放在 `src/tools/`）

要求：
- **输入/输出必须可 JSON 序列化**（str/int/float/bool/list/dict）
- **不要返回二进制大对象**；产物统一写入 `data/`，返回路径字符串
- **建议写 safe wrapper**：永远返回 dict，不直接抛异常（参考 `src/tools/cad_build.py`、`src/tools/sim_tools.py`）

#### 2) 在 `src/agent/tools.py` 增加 Tool wrapper（对 LLM 暴露）

要求：
- 用 `@tool("<tool_name>")` 声明工具名（工具名发布后尽量不改）
- wrapper 负责把底层 dict 结果转成“人类可读 + 关键路径/指标清晰”的文本
- 失败时返回“可行动”的错误（告诉用户/LLM 应该改什么）

#### 3) 注册到 tool pool

在 `src/agent/tools.py` 的 `get_all_tools()` 中追加新工具。

#### 4)（推荐）更新提示词规范

如果新工具是关键能力（例如新增后处理、优化评估、数据检索），建议在 `src/agent/prompts.py` 写清楚：
- 何时必须调用该工具
- 输入字段的单位/默认值
- 出错时的恢复策略（重试/追问/回退）

---

### 工具接口标准化（Tool Contract）

为了让 tool pool 长期可维护、可复现，新增工具建议遵循以下契约：

#### 1) 输入（Input）

- **必须 JSON 可序列化**
- **单位/默认值清晰**（尤其是仿真参数）
- **ID 规范**：
  - `module_id`：标识结构版本；同名覆盖生成 `data/json/<module_id>.json` 与 `data/step/<module_id>.step`
  - `run_id`：标识仿真 case；同名覆盖 `.mph`，但允许 CSV 追加记录历史

#### 2) 输出（Output）

推荐底层函数返回 dict：
- `success: bool`
- 成功：包含关键路径/指标（如 `step_path`、`model_path`、`csv_path`、关键数值等）
- 失败：至少包含 `error`（人类可读），可选 `traceback` / `matlab_output` 等诊断信息

wrapper（对 LLM 暴露的工具）返回建议：
- 1～2 段摘要 + 关键路径/指标列表 + “下一步怎么做”

#### 3) 幂等性与可复现

- 相同输入尽量得到相同输出（或至少输出路径一致、可追踪）
- 产物落盘位置固定（写入 `data/` 的可预期目录）
- 避免隐式随机；需要随机性时显式暴露 `seed`

#### 4) 错误与可恢复性

- 错误信息要“可行动”（告诉贡献者/用户怎么改）
- 区分：
  - **输入错误**（字段缺失、类型错误、几何越界/重叠等）
  - **环境错误**（MATLAB Engine 未安装、COMSOL 路径错误、端口占用等）

---

### 提交规范（建议）

- 一个 PR 做一件事（新增工具 / 修复 bug / 改文档）
- PR 描述里写清楚：
  - 新工具名与用途
  - 输入/输出契约
  - 产物落盘位置（写到 `data/` 的哪里）
  - 是否需要 MATLAB/COMSOL 环境


