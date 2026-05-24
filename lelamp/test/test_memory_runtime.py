from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_bootstrap_agent_runtime_returns_noop_when_disabled(monkeypatch):
    from lelamp.memory import runtime as memruntime

    monkeypatch.setenv("LELAMP_MEMORY_DISABLE", "1")

    runtime = memruntime.bootstrap_agent_runtime(
        SimpleNamespace(model_provider="qwen")
    )

    assert runtime.enabled is False
    runtime.set_motor_bus_enabled(True)
    runtime.close()


def test_bootstrap_agent_runtime_runs_selfcheck_and_starts_session(monkeypatch):
    from lelamp.memory import runtime as memruntime

    events = []

    class FakeWriter:
        pass

    class FakeHandle:
        session_id = "sess_2026-04-18_12-00-00"

        def close(self, *, end_ts_ms=None):
            events.append(("handle.close", end_ts_ms))

    def fake_writer(user_id=None):
        events.append(("writer", user_id))
        return FakeWriter()

    def fake_selfcheck(writer):
        assert isinstance(writer, FakeWriter)
        events.append("selfcheck")
        return SimpleNamespace(recent_index_rebuilt=False)

    def fake_start_agent_session(writer, *, model_providers=(), now=None, pid=None, git_ref=None):
        assert isinstance(writer, FakeWriter)
        events.append(("start_agent_session", tuple(model_providers)))
        return FakeHandle()

    monkeypatch.delenv("LELAMP_MEMORY_DISABLE", raising=False)
    monkeypatch.setattr(memruntime, "MemoryWriter", fake_writer)
    monkeypatch.setattr(memruntime, "run_selfcheck", fake_selfcheck)
    monkeypatch.setattr(memruntime, "start_agent_session", fake_start_agent_session)

    runtime = memruntime.bootstrap_agent_runtime(
        SimpleNamespace(model_provider="glm")
    )

    assert runtime.enabled is True
    assert events == [
        ("writer", None),
        "selfcheck",
        ("start_agent_session", ("glm",)),
    ]


def test_bootstrap_agent_runtime_degrades_to_noop_on_failure(monkeypatch):
    from lelamp.memory import runtime as memruntime

    monkeypatch.delenv("LELAMP_MEMORY_DISABLE", raising=False)
    monkeypatch.setattr(memruntime, "MemoryWriter", lambda user_id=None: (_ for _ in ()).throw(RuntimeError("disk broke")))

    runtime = memruntime.bootstrap_agent_runtime(
        SimpleNamespace(model_provider="qwen")
    )

    assert runtime.enabled is False


def test_agent_memory_runtime_installs_session_listeners_and_records_events():
    from lelamp.memory.runtime import AgentMemoryRuntime

    recorded_calls = []

    class FakeWriter:
        def write_conversation(self, **kwargs):
            recorded_calls.append(("conversation", kwargs))

        def write_function_tool(self, **kwargs):
            recorded_calls.append(("function_tool", kwargs))

    class FakeSession:
        def __init__(self):
            self.callbacks = {}

        def on(self, event, callback=None):
            self.callbacks[event] = callback
            return callback

    runtime = AgentMemoryRuntime(
        enabled=True,
        writer=FakeWriter(),
        session_handle=SimpleNamespace(session_id="sess_2026-04-18_12-00-00"),
    )
    session = FakeSession()

    runtime.install_session_listeners(
        session,
        model_provider="qwen",
        model_name="qwen3.5-omni-plus-realtime",
    )

    session.callbacks["user_input_transcribed"](
        SimpleNamespace(
            transcript="你好呀",
            is_final=True,
            created_at=1713412800.1,
        )
    )
    session.callbacks["conversation_item_added"](
        SimpleNamespace(
            item=SimpleNamespace(role="assistant", text_content="我在呢。"),
            created_at=1713412800.4,
        )
    )
    session.callbacks["function_tools_executed"](
        SimpleNamespace(
            zipped=lambda: [
                (
                    SimpleNamespace(
                        name="express",
                        arguments='{"style":"greeting"}',
                        created_at=1713412800.5,
                    ),
                    SimpleNamespace(
                        output="expression_ok",
                        is_error=False,
                        created_at=1713412800.7,
                    ),
                )
            ]
        )
    )

    assert recorded_calls[0] == (
        "conversation",
        {
            "session_id": "sess_2026-04-18_12-00-00",
            "source": "voice_agent",
            "user_text": "你好呀",
            "assistant_text": "我在呢。",
            "user_text_lang": None,
            "assistant_style": None,
            "turn_duration_ms": 300,
            "model_provider": "qwen",
            "model_name": "qwen3.5-omni-plus-realtime",
            "ts_ms": 1713412800400,
        },
    )
    assert recorded_calls[1] == (
        "function_tool",
        {
            "session_id": "sess_2026-04-18_12-00-00",
            "source": "voice_agent",
            "invoke_id": recorded_calls[1][1]["invoke_id"],
            "phase": "invoke",
            "tool_name": "express",
            "args": {"style": "greeting"},
            "caller": "llm",
            "ts_ms": 1713412800500,
        },
    )
    assert recorded_calls[2] == (
        "function_tool",
        {
            "session_id": "sess_2026-04-18_12-00-00",
            "source": "voice_agent",
            "invoke_id": recorded_calls[1][1]["invoke_id"],
            "phase": "result",
            "tool_name": "express",
            "args": {"style": "greeting"},
            "caller": "llm",
            "duration_ms": 200,
            "ok": True,
            "error": None,
            "ts_ms": 1713412800700,
        },
    )


def test_agent_memory_runtime_extracts_assistant_text_from_content_objects():
    from lelamp.memory.runtime import AgentMemoryRuntime

    recorded_calls = []

    class FakeWriter:
        def write_conversation(self, **kwargs):
            recorded_calls.append(("conversation", kwargs))

        def write_function_tool(self, **kwargs):
            recorded_calls.append(("function_tool", kwargs))

    class FakeSession:
        def __init__(self):
            self.callbacks = {}

        def on(self, event, callback=None):
            self.callbacks[event] = callback
            return callback

    runtime = AgentMemoryRuntime(
        enabled=True,
        writer=FakeWriter(),
        session_handle=SimpleNamespace(session_id="sess_2026-04-18_12-00-00"),
    )
    session = FakeSession()

    runtime.install_session_listeners(
        session,
        model_provider="qwen",
        model_name="qwen3.5-omni-plus-realtime",
    )

    session.callbacks["user_input_transcribed"](
        SimpleNamespace(
            transcript="你好呀",
            is_final=True,
            created_at=1713412800.1,
        )
    )
    session.callbacks["conversation_item_added"](
        SimpleNamespace(
            item=SimpleNamespace(
                role="assistant",
                content=[
                    SimpleNamespace(type="text", text="我在呢。"),
                    SimpleNamespace(type="audio", transcript=""),
                ],
            ),
            created_at=1713412800.4,
        )
    )

    assert recorded_calls == [
        (
            "conversation",
            {
                "session_id": "sess_2026-04-18_12-00-00",
                "source": "voice_agent",
                "user_text": "你好呀",
                "assistant_text": "我在呢。",
                "user_text_lang": None,
                "assistant_style": None,
                "turn_duration_ms": 300,
                "model_provider": "qwen",
                "model_name": "qwen3.5-omni-plus-realtime",
                "ts_ms": 1713412800400,
            },
        )
    ]


def test_agent_memory_runtime_records_auto_expression_fallback():
    from lelamp.memory.runtime import AgentMemoryRuntime

    recorded_calls = []

    class FakeWriter:
        def write_fallback_expression(self, **kwargs):
            recorded_calls.append(("fallback_expression", kwargs))

        def write_function_tool(self, **kwargs):
            recorded_calls.append(("function_tool", kwargs))

    runtime = AgentMemoryRuntime(
        enabled=True,
        writer=FakeWriter(),
        session_handle=SimpleNamespace(session_id="sess_2026-04-18_12-00-00"),
    )
    runtime._last_conversation_event_id = "evt_conv_1"

    runtime.note_auto_expression_fallback(
        style="curious",
        trigger="voice_silence_timeout",
        started_ts_ms=1713412800100,
        ended_ts_ms=1713412800400,
        ok=True,
        error=None,
    )

    assert recorded_calls[0] == (
        "fallback_expression",
        {
            "session_id": "sess_2026-04-18_12-00-00",
            "source": "voice_agent",
            "style": "curious",
            "trigger": "voice_silence_timeout",
            "linked_conversation_event_id": "evt_conv_1",
            "ts_ms": 1713412800100,
        },
    )
    assert recorded_calls[1] == (
        "function_tool",
        {
            "session_id": "sess_2026-04-18_12-00-00",
            "source": "voice_agent",
            "invoke_id": recorded_calls[1][1]["invoke_id"],
            "phase": "invoke",
            "tool_name": "express",
            "args": {"style": "curious"},
            "caller": "auto_expression",
            "ts_ms": 1713412800100,
        },
    )
    assert recorded_calls[2] == (
        "function_tool",
        {
            "session_id": "sess_2026-04-18_12-00-00",
            "source": "voice_agent",
            "invoke_id": recorded_calls[1][1]["invoke_id"],
            "phase": "result",
            "tool_name": "express",
            "args": {"style": "curious"},
            "caller": "auto_expression",
            "duration_ms": 300,
            "ok": True,
            "error": None,
            "ts_ms": 1713412800400,
        },
    )


def test_record_standalone_playback_attaches_writes_and_closes(monkeypatch):
    from lelamp.memory import runtime as memruntime

    events = []

    class FakeWriter:
        pass

    class FakeHandle:
        session_id = "sess_manual_2026-04-18_12-00-00"

        def close(self, *, end_ts_ms=None):
            events.append(("close", end_ts_ms))

    def fake_writer(user_id=None):
        events.append(("writer", user_id))
        return FakeWriter()

    def fake_selfcheck(writer):
        events.append(("selfcheck", isinstance(writer, FakeWriter)))
        return SimpleNamespace()

    def fake_attach_or_create_session(writer):
        events.append(("attach", isinstance(writer, FakeWriter)))
        return FakeHandle()

    def fake_write_playback(self, **kwargs):
        events.append(("write_playback", kwargs))

    monkeypatch.delenv("LELAMP_MEMORY_DISABLE", raising=False)
    monkeypatch.setattr(memruntime, "MemoryWriter", fake_writer)
    monkeypatch.setattr(memruntime, "run_selfcheck", fake_selfcheck)
    monkeypatch.setattr(memruntime, "attach_or_create_session", fake_attach_or_create_session)
    monkeypatch.setattr(FakeWriter, "write_playback", fake_write_playback, raising=False)

    memruntime.record_standalone_playback(
        source="remote_control",
        initiator="remote_control",
        action="play",
        recording_name="curious",
        duration_ms=2034,
        ok=True,
        error=None,
    )

    assert events == [
        ("writer", None),
        ("selfcheck", True),
        ("attach", True),
        (
            "write_playback",
            {
                "session_id": "sess_manual_2026-04-18_12-00-00",
                "source": "remote_control",
                "action": "play",
                "initiator": "remote_control",
                "recording_name": "curious",
                "rgb": None,
                "duration_ms": 2034,
                "ok": True,
                "error": None,
            },
        ),
        ("close", None),
    ]
