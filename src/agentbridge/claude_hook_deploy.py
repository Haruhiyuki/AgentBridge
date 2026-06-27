"""把 AgentBridge 的 Claude Code Hooks 部署到会话工作区。

统一 TUI+PTY 模型下，Claude 会话在 PTY 里跑原生交互 TUI，语义通道靠 Claude Code Hooks
供给。本模块把 hooks 合并进 ``<workspace>/.claude/settings.local.json``，使在该工作区启动的
``claude`` 在交互运行时把 assistant.delta / tool.* / approval / question / turn.completed 等
结构化事件 POST 回 Control Plane，再由 Bot Gateway 流到群里。

合并是幂等的：复用 ``merge_claude_hook_settings`` 先剔除旧的 AgentBridge handler 再写入新
的，保留用户原有的无关设置与其它 hooks。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentbridge.agent_adapter_client import (
    claude_hook_settings_fragment,
    merge_claude_hook_settings,
)

DEFAULT_SETTINGS_RELATIVE_PATH = ".claude/settings.local.json"


def _env_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ClaudeHookDeploymentConfig:
    """从环境变量解析的 Claude Hooks 部署配置。默认关闭，避免无意改写工作区。"""

    enabled: bool = False
    api_url: str | None = None
    api_token: str | None = None
    api_token_file: str | None = None
    hook_command: str = "agentbridge-adapter-client"
    settings_relative_path: str = DEFAULT_SETTINGS_RELATIVE_PATH
    # 交互式提问（AskUserQuestion/审批等）阻塞等待人类作答的上限。默认 300s 对"群里读完三个
    # 问题再 /ab answer"太短，常超时被判 declined。放宽到 ~30 分钟，给真人足够作答时间。
    wait_timeout_seconds: float = 1800.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ClaudeHookDeploymentConfig:
        source = env if env is not None else os.environ
        return cls(
            enabled=_env_flag(source.get("AGENTBRIDGE_CLAUDE_HOOK_DEPLOY")),
            api_url=(
                source.get("AGENTBRIDGE_CLAUDE_HOOK_API_URL")
                or source.get("AGENTBRIDGE_API_URL")
            ),
            api_token=source.get("AGENTBRIDGE_API_TOKEN"),
            api_token_file=source.get("AGENTBRIDGE_API_TOKEN_FILE"),
            hook_command=source.get(
                "AGENTBRIDGE_CLAUDE_HOOK_COMMAND", "agentbridge-adapter-client"
            ),
            settings_relative_path=source.get(
                "AGENTBRIDGE_CLAUDE_HOOK_SETTINGS_PATH", DEFAULT_SETTINGS_RELATIVE_PATH
            ),
            wait_timeout_seconds=float(
                source.get("AGENTBRIDGE_CLAUDE_HOOK_WAIT_TIMEOUT_SECONDS") or 1800
            ),
        )


def _load_existing_settings(settings_path: Path) -> dict[str, object]:
    if not settings_path.exists():
        return {}
    try:
        loaded = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def deploy_claude_hooks(
    *,
    session_id: str,
    workspace_path: str | Path,
    config: ClaudeHookDeploymentConfig,
) -> Path:
    """为某个 Claude 会话写入合并后的 settings 文件，返回写入路径。

    优先用 ``api_token_file`` 引用令牌（不把密钥落进 settings）；只给了裸 token 时才内联
    （include_secret_values=True），适用于本机单用户、settings.local.json 已被 gitignore 的场景。
    """
    settings_path = Path(workspace_path) / config.settings_relative_path
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_existing_settings(settings_path)

    token_file = Path(config.api_token_file) if config.api_token_file else None
    inline_token = config.api_token if (config.api_token and token_file is None) else None
    fragment = claude_hook_settings_fragment(
        hook_command=config.hook_command,
        api_url=config.api_url,
        session_id=session_id,
        api_token=inline_token,
        api_token_file=token_file,
        wait_timeout_seconds=config.wait_timeout_seconds,
        include_secret_values=inline_token is not None,
    )
    merged = merge_claude_hook_settings(existing, fragment)
    settings_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return settings_path
