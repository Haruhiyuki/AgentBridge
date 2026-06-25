from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agentbridge.bot_gateway import BotGatewayService
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, AgentBridgeError, BotPlatform
from agentbridge.onebot import OneBotV11HTTPTransport, onebot_text_payload


@dataclass
class FakePoster:
    calls: list[dict[str, Any]] = field(default_factory=list)
    response: dict[str, Any] = field(
        default_factory=lambda: {"retcode": 0, "data": {"message_id": 42}}
    )

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self.response


def create_session_with_event(control: ControlPlane, tmp_path):
    commands = CommandService(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    commands.execute(
        commands.parse(
            raw_text=f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="onebot-project",
            trace_id="onebot-project",
        )
    )
    session_result = commands.execute(
        commands.parse(
            raw_text="/agent session new OneBot Delivery",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="onebot-session",
            trace_id="onebot-session",
        )
    )
    return context, session_result.data["session_id"]


def test_onebot_text_payload_selects_group_or_private_route():
    control = ControlPlane()
    group_context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    private_context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="private",
        user_id="20002",
    )

    assert onebot_text_payload(group_context, "hello") == (
        "send_group_msg",
        {"group_id": "10001", "message": "hello"},
    )
    assert onebot_text_payload(private_context, "hello") == (
        "send_private_msg",
        {"user_id": "20002", "message": "hello"},
    )


def test_onebot_transport_posts_payload_with_auth_and_idempotency():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    poster = FakePoster()
    transport = OneBotV11HTTPTransport(
        endpoint="http://127.0.0.1:5700/",
        access_token="token",
        poster=poster,
    )

    message_id = transport.send_text(
        platform=BotPlatform.ONEBOT_V11,
        chat_context_id=context.id,
        chat_context=context,
        text="hello",
        idempotency_key="idem-1",
    )

    assert message_id == "onebot:42"
    assert poster.calls == [
        {
            "url": "http://127.0.0.1:5700/send_group_msg",
            "payload": {"group_id": "10001", "message": "hello"},
            "headers": {
                "authorization": "Bearer token",
                "x-agentbridge-idempotency-key": "idem-1",
            },
        }
    ]


def test_onebot_transport_rejects_failed_retcode():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    transport = OneBotV11HTTPTransport(
        endpoint="http://127.0.0.1:5700",
        poster=FakePoster(response={"retcode": 1400, "msg": "failed"}),
    )

    with pytest.raises(AgentBridgeError):
        transport.send_text(
            platform=BotPlatform.ONEBOT_V11,
            chat_context_id=context.id,
            chat_context=context,
            text="hello",
            idempotency_key="idem-1",
        )


def test_bot_gateway_can_deliver_through_onebot_transport(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_event(control, tmp_path)
    poster = FakePoster()
    transport = OneBotV11HTTPTransport(
        endpoint="http://127.0.0.1:5700",
        poster=poster,
    )
    gateway = BotGatewayService(control, transport=transport)

    records = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )

    assert len(records) == 1
    assert records[0].platform_message_id == "onebot:42"
    assert poster.calls[0]["url"] == "http://127.0.0.1:5700/send_group_msg"
    assert "OneBot Delivery" in poster.calls[0]["payload"]["message"]
