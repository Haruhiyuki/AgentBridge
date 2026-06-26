from __future__ import annotations

import json

from agentbridge.claude_hook_deploy import (
    ClaudeHookDeploymentConfig,
    deploy_claude_hooks,
)
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, AgentType, Visibility
from agentbridge.terminal_agent import FakeTerminalBackend, TerminalAgentService


def find_agentbridge_handlers(settings: dict) -> list[dict]:
    found: list[dict] = []
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            for handler in group.get("hooks", []):
                args = handler.get("args")
                if handler.get("type") == "command" and isinstance(args, list) and args[:1] == [
                    "claude-hook"
                ]:
                    found.append(handler)
    return found


def test_deploy_writes_session_scoped_hooks(tmp_path):
    config = ClaudeHookDeploymentConfig(
        enabled=True,
        api_url="http://127.0.0.1:8000",
        api_token_file="/run/agentbridge/api.token",
    )
    path = deploy_claude_hooks(session_id="sess-123", workspace_path=tmp_path, config=config)

    assert path == tmp_path / ".claude" / "settings.local.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    handlers = find_agentbridge_handlers(settings)
    assert handlers, "应写入 AgentBridge 的 claude-hook handler"
    blob = json.dumps(settings, ensure_ascii=False)
    assert "sess-123" in blob
    assert "http://127.0.0.1:8000" in blob
    # 用 token 文件引用而非内联密钥。
    assert "/run/agentbridge/api.token" in blob


def test_deploy_preserves_existing_settings_and_is_idempotent(tmp_path):
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "echo user-own-hook"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ClaudeHookDeploymentConfig(
        enabled=True, api_url="http://h:8000", api_token_file="/t"
    )

    deploy_claude_hooks(session_id="s1", workspace_path=tmp_path, config=config)
    first = json.loads(settings_path.read_text(encoding="utf-8"))
    # 用户原有设置与自有 hook 被保留。
    assert first["permissions"] == {"allow": ["Bash(ls:*)"]}
    user_stop_hooks = [
        h
        for group in first["hooks"]["Stop"]
        for h in group.get("hooks", [])
        if h.get("command") == "echo user-own-hook"
    ]
    assert user_stop_hooks

    # 再次部署不应叠加重复的 AgentBridge handler（幂等）。
    deploy_claude_hooks(session_id="s1", workspace_path=tmp_path, config=config)
    second = json.loads(settings_path.read_text(encoding="utf-8"))
    assert len(find_agentbridge_handlers(second)) == len(find_agentbridge_handlers(first))
    assert user_stop_hooks  # 用户 hook 仍在


def test_config_from_env_defaults_disabled():
    assert ClaudeHookDeploymentConfig.from_env({}).enabled is False
    enabled = ClaudeHookDeploymentConfig.from_env(
        {"AGENTBRIDGE_CLAUDE_HOOK_DEPLOY": "true", "AGENTBRIDGE_API_URL": "http://x"}
    )
    assert enabled.enabled is True
    assert enabled.api_url == "http://x"


def _make_session(control: ControlPlane, tmp_path, agent_type: AgentType):
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor, name="Backend", default_agent=agent_type, trace_id="p"
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="w",
    )
    return control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="S",
        agent_type=agent_type,
        visibility=Visibility.GROUP,
        trace_id="s",
    )


def test_start_session_deploys_hooks_for_claude_only(tmp_path):
    config = ClaudeHookDeploymentConfig(enabled=True, api_url="http://h:8000", api_token_file="/t")

    control = ControlPlane()
    terminal = TerminalAgentService(
        control, backend=FakeTerminalBackend(), claude_hook_deploy=config
    )
    session = _make_session(control, tmp_path, AgentType.CLAUDE)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    assert (tmp_path / ".claude" / "settings.local.json").exists()

    # Codex 会话不部署 Claude hooks。
    codex_dir = tmp_path / "codex_ws"
    codex_dir.mkdir()
    control2 = ControlPlane()
    terminal2 = TerminalAgentService(
        control2, backend=FakeTerminalBackend(), claude_hook_deploy=config
    )
    codex_session = _make_session(control2, codex_dir, AgentType.CODEX)
    terminal2.start_session(session_id=codex_session.id, command="fake-cli", trace_id="start")
    assert not (codex_dir / ".claude" / "settings.local.json").exists()


def test_start_session_skips_deploy_when_disabled(tmp_path):
    control = ControlPlane()
    terminal = TerminalAgentService(
        control,
        backend=FakeTerminalBackend(),
        claude_hook_deploy=ClaudeHookDeploymentConfig(enabled=False),
    )
    session = _make_session(control, tmp_path, AgentType.CLAUDE)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    assert not (tmp_path / ".claude" / "settings.local.json").exists()
