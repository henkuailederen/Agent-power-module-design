from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
import traceback
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Union

from cad.merge import ConfigProcessor

from .cad_schema import validate_device_config
from .json_precheck import run_precheck as _run_cad_json_precheck


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAD_DIR = PROJECT_ROOT / "cad"
DATA_DIR = PROJECT_ROOT / "data"
JSON_TMP_DIR = DATA_DIR / "json"
STEP_OUTPUT_DIR = DATA_DIR / "step"
GENERATED_DIR = DATA_DIR / "py"

logger = logging.getLogger("power_agent.cad_build")


@dataclass
class BuildResult:
    """封装一次 CAD 构建任务的核心输出路径信息。"""

    json_path: Path
    script_path: Path
    step_path: Path


def _ensure_directories() -> None:
    """确保 CAD 相关的工作目录存在。"""
    for d in (GENERATED_DIR, JSON_TMP_DIR, STEP_OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
        logger.debug("确保目录存在：%s", d)


def _normalize_config(config: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    接受 JSON 字符串或字典，统一转为 dict。

    - 如果是字符串，则先尝试按 JSON 解析；
    - 要求字段结构与 cad/reference 目录下示例保持一致，
      这一点由后续 ConfigProcessor 在渲染模板时进行“事实上的校验”；
    """
    if isinstance(config, str):
        try:
            raw_dict = json.loads(config)
        except json.JSONDecodeError as exc:
            raise ValueError(f"提供的 JSON 文本无法解析，请检查是否为合法 JSON：{exc}") from exc
    elif isinstance(config, dict):
        raw_dict = config
    else:
        msg = f"device_config 类型必须是 str 或 dict，但实际得到: {type(config)!r}"
        raise TypeError(msg)

    # 使用 Pydantic 进行结构与类型校验，并返回规整后的 dict
    try:
        normalized = validate_device_config(raw_dict)
        logger.info(
            "DeviceConfig 校验通过，module_id=%s",
            str(normalized.get("module_id")),
        )
        return normalized
    except Exception as exc:
        # 这里捕获的是 pydantic.ValidationError 及其子类，统一包装为 ValueError，便于上层/LLM 处理
        logger.exception("器件 JSON 配置不符合要求：%s", exc)
        raise ValueError(f"器件 JSON 配置不符合要求，请根据错误提示修改：{exc}") from exc


def _generate_task_id(config: Dict[str, Any]) -> str:
    """
    根据配置生成一次任务的 ID：
    - 优先使用 config['module_id']
    - 否则使用随机 UUID 前缀
    """
    module_id = str(config.get("module_id") or "").strip()
    if module_id:
        return module_id
    return uuid.uuid4().hex[:8]


def _render_cad_script(json_path: Path, output_script_path: Path) -> None:
    """
    调用 cad.merge.ConfigProcessor：
    - 读取给定的 JSON 文件
    - 使用同目录下的 template.j2 渲染生成可执行的 CAD Python 脚本
    """
    logger.info("开始渲染 CAD 脚本，输入 JSON：%s", json_path)
    processor = ConfigProcessor(template_filename="template.j2")
    rendered_code = processor.process_json_file(str(json_path))
    output_script_path.write_text(rendered_code, encoding="utf-8")
    logger.info("CAD 脚本已写入：%s", output_script_path)


def _import_and_export_step(script_path: Path, step_output_path: Path) -> None:
    """
    执行生成的 CAD 脚本，并将脚本导出的 STEP 文件移动到统一输出路径。

    约定：
    - 模板脚本内部负责调用 `module.export("<task_id>.step")` 导出 STEP；
    - 本函数只负责：
      1) 以模块方式执行脚本（捕获脚本中的 print 并写入日志）；
      2) 在常见位置查找 `<task_id>.step` 并移动到 `step_output_path`；
      3) 如未找到，则回退使用 cadquery.exporters.export(module.module, ...) 直接导出。
    """
    added_to_syspath = False
    cad_dir_str = str(CAD_DIR)
    if cad_dir_str not in sys.path:
        sys.path.insert(0, cad_dir_str)
        added_to_syspath = True

    try:
        logger.info("开始以模块方式加载 CAD 脚本：%s", script_path)
        spec = importlib.util.spec_from_file_location(
            f"cad_generated_{script_path.stem}", script_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法为脚本创建导入 spec：{script_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module

        # 捕获 CAD 脚本中的 print 输出，写入日志而不是直接打到终端 / 对话
        buf = StringIO()
        with redirect_stdout(buf):
            spec.loader.exec_module(module)  # type: ignore[call-arg]
        stdout_content = buf.getvalue().strip()
        if stdout_content:
            for line in stdout_content.splitlines():
                logger.info("[cad_script stdout] %s", line)

        if not hasattr(module, "module"):
            raise RuntimeError(
                "生成的 CAD 脚本未定义全局变量 `module`，请检查 template.j2 中是否正确设置。"
            )

        task_id = step_output_path.stem
        # 模板已修改为在脚本所在目录导出 STEP，优先在脚本目录查找
        # 同时保留项目根目录查找作为兼容旧行为的后备
        candidate_paths = [
            script_path.parent / f"{task_id}.step",
            PROJECT_ROOT / f"{task_id}.step",
        ]

        for src in candidate_paths:
            if src.exists():
                try:
                    shutil.move(str(src), str(step_output_path))
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "移动脚本自导出的 STEP 文件失败：%s -> %s",
                        src,
                        step_output_path,
                    )
                    raise
                else:
                    logger.info(
                        "检测到脚本自导出的 STEP：%s -> %s", src, step_output_path
                    )
                break
        else:
            # 如果脚本没有自行导出 STEP（例如旧版模板），再退回到 cadquery.exporters.export 方案
            logger.info(
                "未在预期位置找到脚本自导出的 STEP，尝试使用 cadquery.exporters.export。"
            )
            try:
                import cadquery as cq  # type: ignore[import]
                from cadquery import exporters  # type: ignore[import]
            except Exception as exc:  # pragma: no cover - 环境相关
                logger.exception("导出 STEP 时导入 cadquery 失败：%s", exc)
                raise RuntimeError(
                    "导出 STEP 需要安装 cadquery 及其依赖，请确认环境已正确安装。"
                ) from exc

            # module.module 为 cadquery 的 Assembly
            exporters.export(module.module, str(step_output_path))
            logger.info("STEP 文件已导出（fallback 模式）：%s", step_output_path)
    finally:
        if added_to_syspath:
            try:
                sys.path.remove(cad_dir_str)
            except ValueError:
                # 已被其它代码移除，忽略
                pass


def build_step_model_from_config(
    device_config: Union[str, Dict[str, Any]],
) -> BuildResult:
    """
    根据给定的功率器件 JSON 配置，生成 CAD Python 脚本并导出 STEP 文件。

    参数
    ----
    device_config:
        - 可以是 JSON 字符串，也可以是已解析好的 dict。
        - 字段和层级结构必须与 `cad/reference/*.json` 示例严格一致，
          否则在模板渲染阶段可能会抛出异常。

    返回
    ----
    BuildResult:
        - json_path: 实际写入磁盘的 JSON 文件路径（用于追踪与复现）
        - script_path: 渲染得到的 CAD Python 脚本路径
        - step_path: 导出的 STEP 文件路径

    出错时会抛出 ValueError / RuntimeError，调用方可以捕获并返回给上层（包括 LLM）。
    """
    _ensure_directories()

    config_dict = _normalize_config(device_config)
    task_id = _generate_task_id(config_dict)

    # 确保配置中记录此次任务使用的 module_id，便于后续“在原有模块上修改”
    config_dict["module_id"] = task_id

    logger.info("开始构建 STEP 模型，task_id=%s", task_id)

    # 1. 写入 JSON
    json_path = JSON_TMP_DIR / f"{task_id}.json"
    json_path.write_text(
        json.dumps(config_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("配置 JSON 已写入：%s", json_path)

    # 1.5 在进入 CAD merge 之前执行几何预检，避免明显的越界 / 重叠错误
    try:
        precheck_result: Dict[str, Any] = _run_cad_json_precheck(str(json_path))
    except Exception as exc:  # noqa: BLE001
        logger.exception("CAD JSON 预检执行失败：%s", exc)
        raise ValueError(
            f"在构建 STEP 之前执行 CAD JSON 预检失败，请检查 JSON 配置及项目环境：{exc}"
        ) from exc

    if not precheck_result.get("ok", False):
        # 将预检结果打到日志中，便于排查
        logger.warning(
            "CAD JSON 预检未通过：summary=%s, errors=%s",
            precheck_result.get("summary"),
            precheck_result.get("errors"),
        )
        # 直接中止后续 CAD 构建，以免生成明显错误的几何
        summary = precheck_result.get("summary", "CAD JSON 预检未通过")
        errors = precheck_result.get("errors", [])
        raise ValueError(
            "器件 JSON 的几何预检未通过，请根据以下错误信息修改后再试："
            f"{summary}；errors={errors}"
        )

    # 2. 渲染 CAD 脚本
    script_path = GENERATED_DIR / f"{task_id}_cad.py"
    _render_cad_script(json_path, script_path)

    # 3. 执行脚本并导出 STEP
    step_path = STEP_OUTPUT_DIR / f"{task_id}.step"
    _import_and_export_step(script_path, step_path)

    logger.info(
        "STEP 构建完成：json=%s, script=%s, step=%s",
        json_path,
        script_path,
        step_path,
    )

    return BuildResult(json_path=json_path, script_path=script_path, step_path=step_path)


def build_step_model_safe(device_config: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    对 LLM/Agent 更友好的包装：

    - 永远返回一个 dict，而不是直接抛异常；
    - 正常时返回：
        {
            "success": true,
            "step_path": "...",
            "script_path": "...",
            "json_path": "..."
        }
    - 失败时返回：
        {
            "success": false,
            "error": "<人类可读的错误信息>",
            "traceback": "<可选的堆栈信息>"
        }
    """
    try:
        result = build_step_model_from_config(device_config)
        payload = {
            "success": True,
            "step_path": str(result.step_path),
            "script_path": str(result.script_path),
            "json_path": str(result.json_path),
        }
        logger.info("build_step_model_safe 成功：%s", payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        logger.error("构建 STEP 模型失败：%s\n%s", exc, tb)
        return {
            "success": False,
            "error": f"构建 STEP 模型失败: {exc}",
            "traceback": tb,
        }


