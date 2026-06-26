from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agentbridge.bot_gateway import BotGatewayService
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    BotPlatform,
    ChatContext,
    InteractionStatus,
    InteractionType,
    Visibility,
)
from agentbridge.nonebot_plugin import (
    NoneBotAgentBridgePlugin,
    NoneBotTransport,
    nonebot_event_to_onebot_event,
    register_nonebot_command_registration,
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


def create_nonebot_delivery_session(control: ControlPlane, tmp_path):
    commands = CommandService(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    context = control.get_or_create_chat_context(
        bot_instance_id="nonebot-main",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    commands.execute(
        commands.parse(
            raw_text=(
                f"/agent project create --name Backend --path {tmp_path} "
                f"--root {tmp_path}"
            ),
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="nonebot-project",
            trace_id="nonebot-project",
        )
    )
    session_result = commands.execute(
        commands.parse(
            raw_text="/agent session new NoneBot Delivery",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="nonebot-session",
            trace_id="nonebot-session",
        )
    )
    commands.execute(
        commands.parse(
            raw_text="/agent ask render through nonebot",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="nonebot-turn",
            trace_id="nonebot-turn",
        )
    )
    return context, session_result.data["session_id"]


def test_nonebot_transport_delivers_gateway_text_through_send_to(tmp_path):
    class FakeBot:
        def __init__(self) -> None:
            self.sent = []

        def send_to(self, target, message):
            self.sent.append({"target": target, "message": message})
            return {"message_id": 9001}

    control = ControlPlane()
    context, session_id = create_nonebot_delivery_session(control, tmp_path)
    bot = FakeBot()
    gateway = BotGatewayService(control, transport=NoneBotTransport(bot))

    records = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )

    assert len(records) == 1
    assert records[0].platform_message_id == "onebot:9001"
    assert len(bot.sent) == 1
    assert bot.sent[0]["target"]["scope"] == "group"
    assert bot.sent[0]["target"]["group_id"] == "10001"
    assert "任务已排队" in bot.sent[0]["message"]


def test_nonebot_transport_uses_async_call_api_fallback_for_onebot_payloads():
    class FakeAsyncBot:
        def __init__(self) -> None:
            self.calls = []

        async def call_api(self, action, **payload):
            self.calls.append((action, payload))
            return {"retcode": 0, "data": {"message_id": len(self.calls)}}

    bot = FakeAsyncBot()
    transport = NoneBotTransport(bot)
    group_context = ChatContext(
        id="ctx_group",
        bot_instance_id="nonebot-main",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    private_context = ChatContext(
        id="ctx_private",
        bot_instance_id="nonebot-main",
        platform="onebot.v11",
        chat_space_id="private",
        user_id="20002",
    )

    group_message_id = transport.send_text(
        platform=BotPlatform.ONEBOT_V11,
        chat_context_id=group_context.id,
        chat_context=group_context,
        text="hello group",
        idempotency_key="nonebot-group-send",
    )
    private_message_id = transport.send_text(
        platform=BotPlatform.ONEBOT_V11,
        chat_context_id=private_context.id,
        chat_context=private_context,
        text="hello private",
        idempotency_key="nonebot-private-send",
    )
    deleted = transport.delete_message(
        platform=BotPlatform.ONEBOT_V11,
        chat_context_id=private_context.id,
        chat_context=private_context,
        platform_message_id=private_message_id,
        idempotency_key="nonebot-delete",
    )

    assert group_message_id == "onebot:1"
    assert private_message_id == "onebot:2"
    assert deleted["response"]["retcode"] == 0
    assert bot.calls == [
        ("send_group_msg", {"group_id": "10001", "message": "hello group"}),
        ("send_private_msg", {"user_id": "20002", "message": "hello private"}),
        ("delete_msg", {"message_id": 2}),
    ]


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


def test_nonebot_plugin_records_command_registration_results():
    control = ControlPlane()
    plugin = NoneBotAgentBridgePlugin(
        control=control,
        bot_instance_id="nonebot-main",
    )

    manifest = plugin.command_registration_manifest()
    result = plugin.record_command_registration_result(
        status="ok",
        scope="group",
        channel_id="10001",
        registration_id="nonebot-commands-v1",
        payload={"provider": "alconna"},
    )
    repeated = plugin.record_command_registration_result(
        status="ok",
        scope="group",
        channel_id="10001",
        registration_id="nonebot-commands-v1",
        payload={"provider": "alconna"},
    )

    assert manifest["schema_version"] == "bot.command_registration_manifest.v1"
    assert result["event"]["id"] == repeated["event"]["id"]
    assert result["event"]["type"] == "bot.command_registration.result"
    assert result["event"]["payload"]["adapter"] == "nonebot"
    assert result["event"]["payload"]["bot_instance_id"] == "nonebot-main"
    assert result["event"]["payload"]["platform"] == "onebot.v11"
    assert result["event"]["payload"]["status"] == "succeeded"
    assert result["event"]["payload"]["command_count"] > 0
    assert result["event"]["payload"]["payload"] == {"provider": "alconna"}
    events = control.repository.list_semantic_events(
        event_type="bot.command_registration.result",
        trace_id=(
            "bot-command-registration:"
            "onebot.v11:nonebot-main:group:10001:nonebot-commands-v1"
        ),
    )
    assert len(events) == 1


def test_nonebot_plugin_registers_command_manifest_on_startup():
    class FakeDriver:
        def __init__(self) -> None:
            self.startup_handlers = []

        def on_startup(self):
            def decorator(handler):
                self.startup_handlers.append(handler)
                return handler

            return decorator

    driver = FakeDriver()
    control = ControlPlane()
    captured = {}

    async def registrar(manifest):
        captured["manifest"] = manifest
        return {
            "registration_id": "startup-commands-v1",
            "commands": manifest["native_entries"][:2],
            "payload": {"provider": "nonebot-startup"},
        }

    plugin = register_nonebot_command_registration(
        driver,
        registrar,
        control=control,
        bot_instance_id="nonebot-startup",
        scope="group",
        channel_id="10001",
    )
    result = asyncio.run(driver.startup_handlers[0]())

    assert isinstance(plugin, NoneBotAgentBridgePlugin)
    assert len(driver.startup_handlers) == 1
    assert captured["manifest"]["schema_version"] == (
        "bot.command_registration_manifest.v1"
    )
    assert result["event"]["payload"]["status"] == "succeeded"
    assert result["event"]["payload"]["registration_id"] == "startup-commands-v1"
    assert result["event"]["payload"]["command_count"] == 2
    assert result["event"]["payload"]["payload"] == {"provider": "nonebot-startup"}


def test_nonebot_plugin_records_failed_startup_command_registration():
    control = ControlPlane()
    plugin = NoneBotAgentBridgePlugin(
        control=control,
        bot_instance_id="nonebot-failing",
    )

    def registrar(_manifest):
        raise RuntimeError("registration failed")

    handler = plugin.as_command_registration_startup_handler(
        registrar,
        scope="global",
        registration_id="failing-commands-v1",
    )

    try:
        asyncio.run(handler())
    except RuntimeError as exc:
        assert str(exc) == "registration failed"
    else:
        raise AssertionError("expected startup registration failure")

    events = control.repository.list_semantic_events(
        event_type="bot.command_registration.result",
        trace_id=(
            "bot-command-registration:"
            "onebot.v11:nonebot-failing:global:global:failing-commands-v1"
        ),
    )
    assert len(events) == 1
    assert events[0].payload["status"] == "failed"
    assert events[0].payload["error"] == "registration failed"
    assert events[0].payload["payload"] == {"exception_type": "RuntimeError"}


def test_nonebot_matcher_registration_requires_handle_decorator():
    plugin = NoneBotAgentBridgePlugin(control=ControlPlane())

    try:
        plugin.register_matcher(object())
    except TypeError as exc:
        assert "handle()" in str(exc)
    else:
        raise AssertionError("expected invalid matcher to be rejected")
