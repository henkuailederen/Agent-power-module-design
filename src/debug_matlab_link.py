from __future__ import annotations

"""
debug_matlab_link.py
====================

用于在命令行下快速验证：
- 当前 Python 环境是否已正确安装并能导入 MATLAB Engine；
- 能否成功启动 MATLAB 会话；
- 是否能找到工程中的 `sim/run_sim_from_step.m`；
- 实际调用一次 `run_sim_from_step` 时，COMSOL/MATLAB 是否正常工作。

运行方式：在项目根目录执行

    python src/debug_matlab_link.py
"""

import traceback
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    print(f"[INFO] Project root: {project_root}")

    # 1) 测试能否导入 MATLAB Engine
    try:
        import matlab.engine  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001
        print("[ERROR] 无法导入 matlab.engine，请先在 MATLAB 安装目录下安装 Engine for Python。")
        print("        典型命令（需根据你的 MATLAB 版本修改路径）：")
        print('        cd "C:\\Program Files\\MATLAB\\R2023b\\extern\\engines\\python"')
        print("        python -m pip install .")
        print(f"\n具体错误：{exc}")
        return

    print("[OK] 成功导入 matlab.engine 模块。")

    # 2) 通过项目里的封装启动 MATLAB，并加入 sim 路径
    try:
        from src.tools.matlab_session import get_matlab_engine

        eng = get_matlab_engine()
    except Exception as exc:  # noqa: BLE001
        print("[ERROR] 启动 MATLAB Engine 或执行 get_matlab_engine() 失败。")
        print("        请检查：MATLAB 是否已安装、许可证是否可用。")
        print("\nTraceback:")
        print(traceback.format_exc())
        return

    print("[OK] MATLAB Engine 已启动，并已加入 sim 目录到 MATLAB 路径。")

    # 3) 列出 MATLAB 当前路径中是否包含 sim 目录（用于双重确认）
    try:
        paths = eng.path().split(";")  # type: ignore[call-arg]
        sim_dir = str((project_root / "sim").resolve())
        in_path = any(p.strip().lower() == sim_dir.lower() for p in paths)
        print(f"[INFO] MATLAB sim 路径: {sim_dir}")
        print(f"[INFO] sim 目录是否在 MATLAB path 中: {in_path}")
    except Exception:
        # 非关键步骤，出错时仅打印警告
        print("[WARN] 无法从 MATLAB 侧读取 path 信息，该步骤可忽略。")

    # 4) 选一个默认的 STEP 文件名，尝试调用一次 run_sim_from_step
    default_step = "3P6P_V2_default.step"
    step_path = project_root / "data" / "step" / default_step
    print(f"[INFO] 准备调用 run_sim_from_step，STEP 文件名: {default_step}")
    print(f"[INFO] 实际查找路径: {step_path}")
    if not step_path.exists():
        print("[WARN] 默认 STEP 文件在 data/step 下不存在，"
              "如果你有其他 STEP 文件名，请在代码中修改 default_step 再测试。")

    try:
        # 这里只验证调用链是否通畅，不关心返回的 COMSOL Model 对象
        eng.run_sim_from_step(default_step, nargout=1)
    except Exception as exc:  # noqa: BLE001
        print("[ERROR] 调用 run_sim_from_step 失败。")
        print("        这通常意味着：")
        print("        - MATLAB 能启动，但 COMSOL LiveLink 或相关接口未配置好；或")
        print("        - run_sim_from_step.m 中的 COMSOL API 调用报错；或")
        print("        - 默认 STEP 文件不存在/不合法。")
        print("\nTraceback:")
        print(traceback.format_exc())
        return

    print("[OK] 成功调用 run_sim_from_step，MATLAB + COMSOL 链路看起来是畅通的。")


if __name__ == "__main__":
    main()


