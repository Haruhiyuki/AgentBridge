from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, InteractionStatus, InteractionType, Visibility
from agentbridge.nonebot_plugin import (
    NoneBotAgentBridgePlugin,
    nonebot_event_to_onebot_event,
    register_nonebot_matcher,
)


def test_nonebot_plugin_executes_group_text_command():
    control = ControlPlane()
    plugin = NoneBotAgentBridgePlugin(
        control=control,
        bot_instance_id="nonebot-main",
        default_roles={"operator"},
    )

    result = plugin.handle_event(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 10001,
            "user_id": 20002,
            "message_id": 30003,
            "raw_message": "/agent health",
        }
    )

    assert result["handled"] is True
    assert result["result"]["canonical_command"] == "health"
    context = control.repository.get_chat_context(result["chat_context_id"])
    assert context.bot_instance_id == "nonebot-main"
    assert context.chat_space_id == "10001"


@dataclass
class FakeNoneBotEvent:
    group_id: str
    user_id: str
    message_id: str
    text: str

    def get_plaintext(self) -> str:
        return self.text

    def get_user_id(self) -> str:
        return self.user_id


def test_nonebot_event_object_is_normalized_to_onebot_message():
    event = FakeNoneBotEvent(
        group_id="20001",
        user_id="30002",
        message_id="40003",
        text="/agent health",
    )

    payload = nonebot_event_to_onebot_event(event)

    assert payload["post_type"] == "message"
    assert payload["message_type"] == "group"
    assert payload["group_id"] == "20001"
    assert payload["user_id"] == "30002"
    assert payload["message_id"] == "40003"
    assert payload["raw_message"] == "/agent health"


def test_nonebot_plugin_maps_action_callback_to_command(tmp_path):
    control = ControlPlane()
    plugin = NoneBotAgentBridgePlugin(
        control=control,
        bot_instance_id="nonebot-main",
        default_roles={"approver"},
    )
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Callback Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session.id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Allow callback approval?",
        required_votes=1,
        trace_id="approval",
    )

    result = plugin.handle_event(
        {
            "notice_type": "button_clicked",
            "group_id": 10001,
            "user_id": 20002,
            "event_id": "callback-1",
            "data": {"command": f"/agent approve {interaction.id} once"},
        }
    )

    stored = control.get_interaction(actor=maintainer, interaction_id=interaction.id)
    assert result["handled"] is True
    assert result["result"]["canonical_command"] == "approval.vote"
    assert stored.status == InteractionStatus.RESOLVED
    assert stored.votes == {"onebot:20002": True}


def test_nonebot_event_normalizes_nested_action_descriptor_payload():
    payload = nonebot_event_to_onebot_event(
        {
            "notice_type": "button_clicked",
            "group_id": 10001,
            "user_id": 20002,
            "event_id": "callback-nested",
            "data": {
                "action_id": "approve-int_1",
                "payload": {"command": "/agent approve int_1 once"},
            },
        }
    )

    assert payload["post_type"] == "message"
    assert payload["message_type"] == "group"
    assert payload["raw_message"] == "/agent approve int_1 once"
    assert payload["message_id"] == "callback-nested"


def test_nonebot_plugin_ignores_non_command_messages():
    plugin = NoneBotAgentBridgePlugin(control=ControlPlane())

    result = plugin.handle_event(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 10001,
            "user_id": 20002,
            "message_id": 30003,
            "raw_message": "hello",
        }
    )

    assert result == {"handled": False}


def test_nonebot_plugin_async_handler_wraps_sync_bridge():
    async def scenario():
        plugin = NoneBotAgentBridgePlugin(
            control=ControlPlane(),
            default_roles={"operator"},
        )
        handler = plugin.as_async_handler()

        return await handler(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30004,
                "raw_message": "/agent health",
            }
        )

    result = asyncio.run(scenario())

    assert result["handled"] is True
    assert result["result"]["canonical_command"] == "health"


def test_nonebot_matcher_registration_helper_registers_async_handler():
    class FakeMatcher:
        def __init__(self) -> None:
            self.handlers = []

        def handle(self):
            def decorator(handler):
                self.handlers.append(handler)
                return handler

            return decorator

    matcher = FakeMatcher()
    control = ControlPlane()

    plugin = register_nonebot_matcher(
        matcher,
        control=control,
        bot_instance_id="nonebot-helper",
        default_roles={"operator"},
    )

    assert isinstance(plugin, NoneBotAgentBridgePlugin)
    assert len(matcher.handlers) == 1
    result = asyncio.run(
        matcher.handlers[0](
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30005,
                "raw_message": "/agent health",
            }
        )
    )
    context = control.repository.get_chat_context(result["chat_context_id"])
    assert result["handled"] is True
    assert result["result"]["canonical_command"] == "health"
    assert context.bot_instance_id == "nonebot-helper"


def test_nonebot_matcher_registration_requires_handle_decorator():
    plugin = NoneBotAgentBridgePlugin(control=ControlPlane())

    try:
        plugin.register_matcher(object())
    except TypeError as exc:
        assert "handle()" in str(exc)
    else:
        raise AssertionError("expected invalid matcher to be rejected")
