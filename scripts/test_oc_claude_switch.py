"""oc_claude model/effort switch on an existing Claude session (oc_seat resume / oc_steer --model/--variant), offline
against the scripted fake host of test_oc_claude. Never touches the live host."""
import pytest

import oc_backend
import oc_claude
from oc_backend import SeatError
from test_oc_claude import Clock, FakeHost, S, eid_of, live, nosleep


@pytest.fixture(autouse=True)
def isolated_host_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(tmp_path / "no-host.sock"))
    monkeypatch.delenv("OC_CLAUDE_BACKEND", raising=False)


class SwitchHost(FakeHost):
    """FakeHost plus the host's seat-switch setters: each applies and reads back like controls._settle_applied."""

    def __init__(self, tmp_path, *records, switches=True, readback=None):
        super().__init__(tmp_path, *records)
        if switches:
            self.health["seat_settings_switch"] = 1
        self.readback = readback or {}

    def _set(self, execution_id, key, value, authority):
        assert authority.startswith("seat-switch:")
        rec = self.records[execution_id]
        rec["requested_" + key] = value
        read = {"model": rec["requested_model"], "effort": rec["requested_effort"], **self.readback}
        state = "applied" if read[key] == value else "mismatch"
        if state == "applied":
            rec.update(actual_model=read["model"], actual_effort=read["effort"])
        return {"state": state, "readback": read, "disclosure": None if state == "applied" else "differs"}

    def m_set_model(self, execution_id, generation, model, authority):
        return self._set(execution_id, "model", model, authority)

    def m_apply_effort(self, execution_id, generation, effort, authority):
        return self._set(execution_id, "effort", effort, authority)


FABLE = dict(agent="quick-fable51-xhigh", requested_model="claude-fable-5-1", requested_effort="xhigh",
             last_input_uuid="m")


def test_switch_choice_parses_and_refuses():
    assert oc_claude.switch_choice() == {"model": None, "effort": None}
    assert oc_claude.switch_choice("anthropic/claude-opus-5-5", None) == {"model": "claude-opus-5-5", "effort": None}
    assert oc_claude.switch_choice("anthropic/claude-opus-5-5#xhigh") == {"model": "claude-opus-5-5", "effort": "xhigh"}
    assert oc_claude.switch_choice(None, "max") == {"model": None, "effort": "max"}
    for bad in (("openai/gpt-5", None), ("anthropic/claude-opus-5-5#high", "max"), ("claude-opus-5-5", None),
                ("anthropic/gpt-5", None)):
        with pytest.raises(SeatError):
            oc_claude.switch_choice(*bad)


def test_follow_up_switches_fable_to_opus_before_sending(tmp_path):
    eid = eid_of()
    host = SwitchHost(tmp_path, live(**FABLE))
    lines = []
    res = oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lines.append, sleep=nosleep, poll=0,
                              clock=Clock(), switch={"model": "claude-opus-5-5", "effort": "xhigh"})
    methods = host.methods()
    assert "set_model" in methods and "apply_effort" not in methods        # xhigh already: only the model changes
    assert methods.index("set_model") < methods.index("send") and host.sent == ["next"]
    assert res["switched"] == {"model": "claude-opus-5-5"}
    assert host.records[eid]["requested_model"] == "claude-opus-5-5"
    assert any("applied.model=claude-opus-5-5" in line for line in lines)
    assert "claude-opus-5-5 · xhigh" in lines[-1]


def test_follow_up_switch_resumes_a_gone_seat_first(tmp_path):
    eid = eid_of()
    host = SwitchHost(tmp_path, live(owner="gone", state="exited", **FABLE))
    res = oc_claude.follow_up(eid, "again", host=host, settings=S(), out=lambda _: None, sleep=nosleep, poll=0,
                              clock=Clock(), switch={"model": "claude-opus-5-5", "effort": "max"})
    m = host.methods()
    assert res["resumed"] and m.index("resume") < m.index("set_model") < m.index("apply_effort") < m.index("send")


def test_follow_up_switch_refusals_send_nothing(tmp_path):
    eid = eid_of()
    old = SwitchHost(tmp_path, live(**FABLE), switches=False)               # a host from before the change
    with pytest.raises(SeatError, match="next guarded host restart"):
        oc_claude.follow_up(eid, "x", host=old, settings=S(), out=lambda _: None, sleep=nosleep,
                            switch={"model": "claude-opus-5-5", "effort": None})
    assert "send" not in old.methods() and "set_model" not in old.methods()
    full = SwitchHost(tmp_path, live(context_windows={"claude-opus-5-5": 200_000}, **FABLE))
    full.context = {"ok": True, "occupancy_tokens": 300_000, "window_tokens": 1_000_000, "fraction_used": .3,
                    "target": {"fraction": .65, "source": "profile", "crossed": False}, "label": "measured"}
    with pytest.raises(SeatError) as e:
        oc_claude.follow_up(eid, "x", host=full, settings=S(), out=lambda _: None, sleep=nosleep,
                            switch={"model": "claude-opus-5-5", "effort": None})
    assert e.value.exit_code == oc_backend.EXIT_CONTEXT and "send" not in full.methods()
    bad = SwitchHost(tmp_path, live(**FABLE), readback={"model": "claude-fable-5-1"})   # the native kept Fable
    with pytest.raises(SeatError, match="mismatch"):
        oc_claude.follow_up(eid, "x", host=bad, settings=S(), out=lambda _: None, sleep=nosleep, poll=0,
                            clock=Clock(), switch={"model": "claude-opus-5-5", "effort": None})
    assert "send" not in bad.methods()


def test_no_op_switch_and_settings_line(tmp_path):
    eid = eid_of()
    host = SwitchHost(tmp_path, live(**FABLE))
    res = oc_claude.follow_up(eid, "x", host=host, settings=S(), out=lambda _: None, sleep=nosleep, poll=0,
                              clock=Clock(), switch={"model": "claude-fable-5-1", "effort": "xhigh"})
    assert res["switched"] == {} and "set_model" not in host.methods()
    rec = live(launch={"model": "claude-fable-5-1", "effort": "xhigh"}, actual_model="claude-opus-5-5",
               actual_effort="xhigh", requested_model="claude-opus-5-5", requested_effort="xhigh")
    line = oc_claude.settings_line(rec)
    assert line.startswith("model: claude-opus-5-5 · xhigh (native readback)") and "from launch claude-fable-5-1" in line
