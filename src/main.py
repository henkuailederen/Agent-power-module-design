from typing import List
from datetime import datetime

import logging
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from .agent import build_cli_agent


# === 日志初始化 ===
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 为每次会话生成带时间戳的日志文件
SESSION_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"agent_{SESSION_TIMESTAMP}.log"

# 清理旧日志：只保留最近 10 个会话的日志文件
_log_files = sorted(LOG_DIR.glob("agent_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
if len(_log_files) > 10:
    for old_log in _log_files[10:]:
        try:
            old_log.unlink()
        except Exception:  # noqa: BLE001
            pass  # 删除失败不影响主流程

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="w"),  # 每个会话新建文件，不追加
    ],
)
logger = logging.getLogger("power_agent.cli")
logger.info(f"=== 新会话启动，日志文件：{LOG_FILE.name} ===")


def run_cli_chat() -> None:
    """命令行多轮对话入口。"""
    print("===== Power Device Agent Demo =====")
    print("输入你的问题，输入 exit / quit 退出。\n")
    print("提示：你可以让智能体根据参考 JSON 构造新的功率模块，并自动生成 STEP 模型。\n")

    logger.info("=== 启动 CLI 多轮对话 ===")

    agent = build_cli_agent()
    history: List[HumanMessage | AIMessage] = []

    while True:
        try:
            user_input = input("你：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见 👋")
            logger.info("用户中断会话，CLI 退出。")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("会话结束，再见 👋")
            logger.info("用户输入 exit/quit，CLI 退出。")
            break
        if not user_input:
            continue

        logger.info("用户输入：%s", user_input)

        # 在调用智能体前先打印前缀，后续内容由 LLM 的流式回调实时输出
        print("智能体：", end="", flush=True)

        try:
            result = agent.invoke({"history": history, "input": user_input})
        except Exception as e:  # noqa: BLE001
            logger.exception("调用智能体失败：%s", e)
            print(f"\n[错误] 调用智能体失败：{e}")
            continue

        if isinstance(result, AIMessage):
            ai_msg = result
        else:
            # 一般不会走到这里，但做个兜底
            ai_msg = AIMessage(content=str(result))

        # 日志中仍记录完整回复内容，终端输出依赖流式回调
        logger.info("智能体回复：%s", ai_msg.content)
        print()  # 确保和下一轮交互之间有一个空行

        history.append(HumanMessage(content=user_input))
        history.append(ai_msg)


if __name__ == "__main__":
    run_cli_chat()
