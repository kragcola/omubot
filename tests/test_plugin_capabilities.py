from kernel.types import PluginContext


def test_service_capabilities_reflect_legacy_context_fields() -> None:
    message_log = object()
    timeline = object()
    scheduler = object()
    tool_registry = object()
    persona_runtime = object()
    ctx = PluginContext(
        msg_log=message_log,
        timeline=timeline,
        scheduler=scheduler,
        tool_registry=tool_registry,
        persona_runtime=persona_runtime,
    )

    capabilities = ctx.service_capabilities

    assert capabilities.conversation.message_log is message_log
    assert capabilities.conversation.timeline is timeline
    assert capabilities.conversation.scheduler is scheduler
    assert capabilities.runtime.tool_registry is tool_registry
    assert capabilities.persona.persona_runtime is persona_runtime


def test_service_capabilities_reflect_late_runtime_assignments() -> None:
    ctx = PluginContext()
    first = ctx.service_capabilities
    capture = object()
    guard = object()

    ctx.research_event_capture = capture
    ctx.outbound_group_access_guard = guard
    second = ctx.service_capabilities

    assert first.conversation.research_event_capture is None
    assert second.conversation.research_event_capture is capture
    assert second.runtime.outbound_group_access_guard is guard


def test_service_capabilities_does_not_expand_plugin_context_fields() -> None:
    assert "service_capabilities" not in PluginContext.__dataclass_fields__
