## Power Module Agent: Power Module Packaging Design + CAD + Thermal Simulation + Optimization Agent

> An **LLM + Tool Pool** driven automation pipeline for power module design: **parametric CAD (STEP)** + **MATLAB–COMSOL co-simulation (.mph)** + **automated post-processing (chip max temperature CSV)** + **pluggable optimization (SA now, extensible later)**.

Language: [中文 README](README.md) | **English**

---

### Table of Contents

- [Key Features](#key-features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quick Start](#quick-start)
- [Extending the Tool Pool](#extending-the-tool-pool)
- [Tool Interface Contract (Standardization Guide)](#tool-interface-contract-standardization-guide)
- [Project Layout](#project-layout)
- [FAQ / Troubleshooting](#faq--troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

### Key Features

- **End-to-end closed loop**: connects “parameters → CAD → simulation → post-processing → metrics → iteration/optimization” to reduce manual, repetitive work.
- **Template-driven configuration**: built-in reference templates `3P6P_V1/3P6P_V2/HB_V1~HB_V4` under `cad/reference/`, enabling fast “template + overrides” generation.
- **Strong validation & prechecks**:
  - **Schema validation** via Pydantic before CAD build (see `src/tools/cad_schema.py`).
  - **Geometry precheck** for chip out-of-zone and chip overlap with actionable suggestions (see `src/tools/json_precheck.py`).
- **JSON → STEP CAD pipeline**: automatically renders a CAD Python script and exports STEP; also persists reproducible artifacts under `data/json/` and `data/py/`.
- **MATLAB–COMSOL co-simulation automation**:
  - STEP → solve → save `.mph` (tool `run_thermal_sim_from_step`).
  - `.mph` → chip max temperature statistics → append CSV records (tool `compute_chip_maxT_from_mph`).
- **Reproducible artifact management**:
  - `module_id` identifies a design version (overwrites `data/json/<module_id>.json` and `data/step/<module_id>.step`).
  - `run_id` identifies a simulation case (overwrites `<run_id>_thermal.mph`, appends to `<run_id>_thermal_chip_maxT.csv`).
- **Pluggable optimization**: algorithm-agnostic `opt_*` session interface (SA implemented today), designed to connect objective evaluation to real simulations or historical data.
- **Highly extensible Tool Pool**: add new capabilities (post-processing, datasets, rules, retrieval, visualization) as tools and let the agent orchestrate them.
- **Multi-round tool orchestration**: a simple but robust tool-calling loop to chain multiple tools until convergence (see `src/agent/workflows.py`).

---

### Architecture

This repository intentionally separates “engineering capabilities” from “agent orchestration” for maintainability and extensibility:

- **Core capability layer (reusable)**: `src/tools/`
  - CAD: `template_merge.py`, `cad_build.py`, `json_precheck.py`
  - Simulation: `matlab_session.py` (MATLAB Engine + COMSOL attach/boot), `sim_tools.py` (simulation + post-processing wrappers)
  - Optimization: `opt/` (SA kernel today)
- **Tool Pool (LLM-facing tools)**: `src/agent/tools.py`
  - Wrap capability functions with `@tool` and register them in `get_all_tools()`
- **Workflow/orchestration layer**: `src/agent/workflows.py`
  - Binds the tool list via `llm.bind_tools(get_all_tools())`
  - Runs the loop: LLM → tool_calls → execute tools → ToolMessage → LLM

In short: **`src/tools` defines what the system can do; `src/agent` defines how the LLM can use it.**

---

### Prerequisites

There are two tracks:

#### 1) Python-only (CAD/JSON)

- **Python**: recommended 3.9+
- **Dependencies**: `requirements.txt`
- **CadQuery**: for parametric CAD and STEP export (`cadquery>=2.5`)

Note: On Windows, CadQuery may be easier to install via Conda depending on OCC dependencies.

#### 2) Co-simulation (optional, required for simulation)

- **MATLAB**
- **MATLAB Engine API for Python**
- **COMSOL Multiphysics**
- **LiveLink™ for MATLAB®**

The project starts MATLAB Engine from Python and calls `mphstart` to connect to COMSOL (it may also try to start a local `comsolmphserver`).

---

### Installation

1) Clone repository

- `git clone <YOUR_REPO_URL>`
- `cd Power-Module-Agent`

2) Create a virtual environment and install dependencies (Windows PowerShell)

- `python -m venv .venv`
- `.venv\Scripts\Activate.ps1`
- `pip install -r requirements.txt`

3) (Optional) Install MATLAB Engine for Python

- `cd "C:\Program Files\MATLAB\R2023b\extern\engines\python"` (adjust to your MATLAB version)
- `python -m pip install .`

4) (Optional) Configure COMSOL LiveLink paths/port

Defaults follow a COMSOL 6.2 Windows installation. If your installation differs, override via environment variables:

- `COMSOL_MLI_DIR` (e.g., `C:\Program Files\COMSOL\COMSOL62\Multiphysics\mli`)
- `COMSOL_BIN_DIR` (e.g., `C:\Program Files\COMSOL\COMSOL62\Multiphysics\bin\win64`)
- `COMSOL_PORT` (default `2036`)

These are read by `src/tools/matlab_session.py`.

---

### Configuration

#### 1) LLM provider (OpenAI-compatible)

Create a `.env` file at repo root (do not commit it).

You can copy `env.example` to `.env` first, then edit:

- `OPENAI_API_KEY=...`
- `OPENAI_BASE_URL=...`
- `OPENAI_MODEL=...`

---

### Quick Start

#### 1) Start the CLI agent

- Run: `python -m src.main`
- Exit: type `exit` or `quit`

#### 2) Generate STEP via the toolchain

Recommended minimal flow:

- Choose a template (e.g., `3P6P_V1`) and provide a small set of overrides (must include `module_id`)
- Outputs:
  - `data/json/<module_id>.json`
  - `data/py/<module_id>_cad.py`
  - `data/step/<module_id>.step`

#### 3) (Optional) Run thermal simulation and compute chip max temperature

Simulation toolchain wrappers are in `src/tools/sim_tools.py`:

- STEP → solve & save `.mph`: tool `run_thermal_sim_from_step`
- `.mph` → chip max temperature statistics: tool `compute_chip_maxT_from_mph`

Notes:
- `.mph` artifacts are saved under `data/sim_results/`. The same `run_id` overwrites `<run_id>_thermal.mph`.
- Chip max temperature results are written to `*_chip_maxT.csv` in append mode for history comparison.

---

### Extending the Tool Pool

You can turn any automatable engineering action into a tool and add it into the tool pool. The registration entry is `src/agent/tools.py`, and the workflow binds tools from `get_all_tools()` (see `src/agent/workflows.py`).

Recommended principles:

- Put reusable capability functions under **`src/tools/`**
- Prefer **JSON-serializable inputs/outputs**
- Large artifacts should be persisted under **`data/`** and returned as paths
- Tool wrappers should return **short, human-readable summaries** plus key paths/metrics

How to add a new tool:

1) Implement the capability in `src/tools/` (prefer a safe wrapper that returns a dict with `success` and `error`)
2) Add an LLM-facing wrapper in `src/agent/tools.py` using `@tool("<tool_name>")`
3) Register it in `get_all_tools()`
4) (Recommended) Update `src/agent/prompts.py` to document when to use the tool and how to recover from failures

For detailed contribution guidelines, see `CONTRIBUTING.en.md`.

---

### Tool Interface Contract (Standardization Guide)

To keep the tool pool **scalable, reproducible, and maintainable**, we recommend a consistent tool contract across the repo.

#### 1) Naming and responsibility

- Keep tool names stable once published (compatibility).
- Use verb-first naming: `build_*`, `run_*`, `compute_*`, `export_*`, `validate_*`, `list_*`.
- Prefer single responsibility; let the workflow compose tools.

#### 2) Input contract

- Inputs should be JSON-serializable primitives: `str/int/float/bool/list/dict`.
- Document units and defaults (especially for simulation parameters).
- ID conventions:
  - `module_id` identifies a design version and should overwrite `data/json/<module_id>.json` and `data/step/<module_id>.step`.
  - `run_id` identifies a simulation case and should overwrite `.mph` while allowing CSV append.
- Validate early (schema and precheck) to fail fast with actionable errors.

#### 3) Output contract

We recommend capability functions return a dict:

- `success: bool`
- On success: include key paths/metrics (e.g., `step_path`, `model_path`, `csv_path`, numeric results)
- On failure: include at least `error` (human-readable); optionally `traceback` / `matlab_output` for diagnostics

LLM-facing wrappers should return:

- a short summary (1–2 paragraphs)
- key paths/metrics as a list
- “what to do next” guidance

#### 4) Idempotency & reproducibility

- Same inputs should produce consistent outputs (or at least consistent output paths).
- Persist artifacts under predictable `data/` locations.
- Avoid hidden randomness; expose `seed` explicitly when needed.

#### 5) Error handling & recoverability

- Error messages must be actionable.
- Distinguish input errors (fix parameters/config) from environment errors (install/configure MATLAB/COMSOL, fix paths/ports).
- Split long pipelines so failures preserve context (e.g., simulation vs post-processing).

#### 6) Observability

- Log key events (tool name, key params with no secrets, artifact paths, duration, error summary).
- Include correlation identifiers such as `module_id`, `run_id`, and `session_id`.

---

### Project Layout

- `cad/`: CAD templates and rendering utilities
  - `cad/reference/`: reference templates (file extension is `.json` but content is YAML)
  - `cad/template.j2`: CAD script template
- `data/`: reproducible artifacts and intermediate outputs
  - `data/json/`: device configs (named by `module_id`)
  - `data/py/`: rendered CAD Python scripts
  - `data/step/`: exported STEP models
  - `data/sim_results/`: `.mph` and post-processing CSV outputs
  - `data/opt/`: optimization histories/meta
- `sim/`: MATLAB scripts (e.g., `run_sim_from_step.m`, `compute_chip_maxT.m`)
- `src/`: Python sources
  - `src/main.py`: CLI entry
  - `src/config.py`: LLM configuration (from `.env` / env vars)
  - `src/agent/`: prompts/workflows/tool wrappers
  - `src/tools/`: CAD/simulation/optimization capability functions

---

### FAQ / Troubleshooting

#### Cannot import `matlab.engine`

- Install MATLAB Engine for Python (see Installation) and verify:
  - `python -m src.debug_matlab_link`

#### `mphstart` fails / COMSOL connection issues

- Ensure LiveLink™ for MATLAB® is installed.
- Verify `COMSOL_MLI_DIR`, `COMSOL_BIN_DIR`, and `COMSOL_PORT`.
- Ensure the port is not occupied.

#### CadQuery installation issues

CadQuery may be sensitive to OCC dependencies on some platforms. Consider using Conda or follow CadQuery’s official installation guidance.

---

### Contributing

- See `CONTRIBUTING.en.md` (English) or `CONTRIBUTING.md` (中文).

---

### License

This project is licensed under the **MIT License**. See `LICENSE`.

---

### Acknowledgements

- CadQuery (parametric CAD / STEP export)
- LangChain (tool calling & agent framework)
- MATLAB Engine API for Python
- COMSOL Multiphysics & LiveLink for MATLAB


