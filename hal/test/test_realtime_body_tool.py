"""`body_action` realtime tool: registered only with a Booster body; walking needs a request."""
import json
import os
import sys
from types import SimpleNamespace

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import hal.realtime.orchestrator as orch  # noqa: E402
from hal.realtime.models import FunctionCallOutput  # noqa: E402


class FakeAgent:
    def __init__(self):
        self.sent = []

    def send(self, inputs):
        self.sent.extend(inputs)


def make(monkeypatch):
    fired = []
    monkeypatch.setattr(orch.RealtimeOrchestrator, "_fire_body_action",
                        staticmethod(lambda action: fired.append(action)))
    o = orch.RealtimeOrchestrator.__new__(orch.RealtimeOrchestrator)
    o._agent = FakeAgent()
    return o, fired


def call(o, action, transcript):
    o._handle_body_call(FunctionCallOutput(
        name=orch.BODY_TOOL_NAME, arguments=json.dumps({"action": action}),
        call_id="c1", user_transcript=transcript,
    ))


def wait_for(fired, n=1):
    import time
    end = time.monotonic() + 1
    while len(fired) < n and time.monotonic() < end:
        time.sleep(0.01)


def test_gesture_fires_without_a_request(monkeypatch):
    o, fired = make(monkeypatch)
    call(o, "wave", "Hello there!")
    wait_for(fired)
    assert fired == ["wave"]
    assert "started" in o._agent.sent[-1].output


def test_walk_refused_unless_the_user_asked(monkeypatch):
    o, fired = make(monkeypatch)
    call(o, "step_forward", "What a nice day.")
    assert fired == []
    assert "did not ask you to move" in o._agent.sent[-1].output


def test_walk_allowed_when_asked(monkeypatch):
    o, fired = make(monkeypatch)
    for said, action in (("Can you come closer?", "step_forward"), ("Please turn around", "turn_left"),
                         ("Step back a bit", "step_back")):
        call(o, action, said)
    wait_for(fired, 3)
    assert sorted(fired) == sorted(["step_forward", "turn_left", "step_back"])


def test_unknown_action_is_an_error(monkeypatch):
    o, fired = make(monkeypatch)
    call(o, "backflip", "do a backflip")
    assert fired == [] and "error" in o._agent.sent[-1].output


def test_tool_registered_only_with_a_body(monkeypatch):
    import hal.app_state as state

    monkeypatch.setattr(state, "animation_service", SimpleNamespace(), raising=False)
    assert orch._body_present() is False
    monkeypatch.setattr(state, "animation_service", SimpleNamespace(body=object()), raising=False)
    assert orch._body_present() is True


def _make_head(monkeypatch):
    fired = []
    monkeypatch.setattr(orch.RealtimeOrchestrator, "_fire_head_action",
                        staticmethod(lambda action: fired.append(action)))
    o = orch.RealtimeOrchestrator.__new__(orch.RealtimeOrchestrator)
    o._agent = FakeAgent()
    return o, fired


def test_head_moves_fire_without_a_movement_request(monkeypatch):
    """Head glances are small and safe: no 'only when asked' gate, unlike walking."""
    o, fired = _make_head(monkeypatch)
    for action in ("look_left", "center", "nod"):
        o._handle_head_call(FunctionCallOutput(
            name=orch.HEAD_TOOL_NAME, arguments=json.dumps({"action": action}),
            call_id="h1", user_transcript="Turn it.",
        ))
    wait_for(fired, 3)
    assert sorted(fired) == ["center", "look_left", "nod"]
    assert "started" in o._agent.sent[-1].output


def test_head_unknown_action_is_an_error(monkeypatch):
    o, fired = _make_head(monkeypatch)
    o._handle_head_call(FunctionCallOutput(
        name=orch.HEAD_TOOL_NAME, arguments=json.dumps({"action": "spin"}), call_id="h2",
    ))
    assert fired == [] and "error" in o._agent.sent[-1].output


def test_head_left_is_negative_yaw():
    # HAL convention (verified on a K1): negative yaw turns the head left.
    assert orch._HEAD_NUDGES["look_left"][0] < 0 < orch._HEAD_NUDGES["look_right"][0]
    assert orch._HEAD_NUDGES["look_up"][1] > 0 > orch._HEAD_NUDGES["look_down"][1]
