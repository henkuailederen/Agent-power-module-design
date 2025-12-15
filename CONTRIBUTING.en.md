## Contributing

Thank you for contributing to **Power Module Agent**. The core goal of this repository is to make power-module packaging workflows **reproducible, composable, and extensible** via a Tool Pool that covers **CAD (STEP)**, **MATLAB–COMSOL co-simulation**, **post-processing**, and **optimization**.

Language: [中文贡献指南](CONTRIBUTING.md) | **English**

---

### Recommended development environment

- **Python**: 3.9+ recommended
- **Install dependencies**: `pip install -r requirements.txt`
- **Optional (simulation toolchain)**:
  - MATLAB
  - MATLAB Engine API for Python
  - COMSOL Multiphysics + LiveLink™ for MATLAB®

Environment variables:
- Copy `env.example` to `.env` and fill in values (do not commit `.env`).

---

### Where to extend (project anatomy)

- **Capability layer (reusable)**: `src/tools/`
- **Tool Pool registration**: `src/agent/tools.py` (`get_all_tools()`)
- **Tool orchestration workflow**: `src/agent/workflows.py`
- **Behavior & usage conventions**: `src/agent/prompts.py`

---

### How to add a new tool (recommended workflow)

Goal: add a new capability as a tool and register it into the Tool Pool so the agent can call it.

#### 1) Implement the capability under `src/tools/`

Requirements:
- Inputs/outputs must be **JSON-serializable** (`str/int/float/bool/list/dict`)
- Do not return large binary objects; persist artifacts under `data/` and return paths
- Prefer a **safe wrapper** that returns a dict and does not throw (see `src/tools/cad_build.py`, `src/tools/sim_tools.py`)

#### 2) Add an LLM-facing wrapper in `src/agent/tools.py`

Requirements:
- Use `@tool("<tool_name>")` to define a stable tool name (avoid renaming after release)
- The wrapper should convert the structured dict result into a short, human-readable summary
- On failures, return actionable guidance (what to fix / what to configure)

#### 3) Register the tool into the Tool Pool

Append the new tool to the list returned by `get_all_tools()` in `src/agent/tools.py`.

#### 4) (Recommended) Update the prompt conventions

If the tool is a key capability (post-processing, objective evaluation, retrieval, etc.), update `src/agent/prompts.py` to clarify:
- when the tool must be used
- units/defaults for inputs
- error recovery strategy (retry / ask user / fallback)

---

### Tool interface contract (standardization)

To keep the Tool Pool scalable and maintainable, new tools should follow a consistent contract.

#### 1) Inputs

- Must be JSON-serializable primitives
- Units and defaults must be explicit
- ID conventions:
  - `module_id`: identifies a design version; same id overwrites `data/json/<module_id>.json` and `data/step/<module_id>.step`
  - `run_id`: identifies a simulation case; same id overwrites `.mph` while allowing CSV append for history

#### 2) Outputs

Capability functions should return a dict:
- `success: bool`
- On success: key paths/metrics (`step_path`, `model_path`, `csv_path`, numeric results, etc.)
- On failure: at least `error` (human-readable); optionally diagnostics (`traceback`, `matlab_output`, etc.)

Tool wrappers should return:
- short summary (1–2 paragraphs)
- key paths/metrics in a list
- next-step suggestions

#### 3) Idempotency & reproducibility

- Same input should lead to consistent outcomes (or at least consistent output paths)
- Persist artifacts under predictable `data/` locations
- Avoid hidden randomness; expose `seed` when applicable

#### 4) Error handling & recoverability

- Errors must be actionable
- Distinguish:
  - input/config errors (fix parameters/JSON)
  - environment errors (install/configure MATLAB/COMSOL, fix paths/ports)

---

### PR guidance (recommended)

- Keep each PR focused (one feature/fix/doc change)
- In the PR description, include:
  - tool name and purpose
  - input/output contract
  - artifact locations under `data/`
  - whether MATLAB/COMSOL is required to use/test the tool


