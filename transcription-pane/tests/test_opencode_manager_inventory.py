"""OpenCode inventory: ordinary genuine Managers in the sidebar's rows, and the shared Manager authority.

The one Manager rule is `oc_running.is_manager_session`: a root session on an actual manager/manager-gpt
agent with no seat/worker/helper/conductor/warm/quick/program/fork ownership stamp. A Manager-Session name
or a manager-sessions folder alone never makes one. `program_rows` projects every nonparked session;
the one shared universe, not a second age filter here, decides which whole units a view lists.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "transcription-pane"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_running as R
from opencode_programs import program_rows

NOW = 1_792_090_000.0


def session(sid, *, title="Manager-Session-193", agent="manager", metadata=None, parent=None,
            created=NOW - 3600, directory="/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions", **extra):
    s = {"id": sid, "title": title, "agent": agent,
         "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "high"},
         "time": {"created": created * 1000, "updated": created * 1000, "idle": created * 1000 + 5000},
         "location": {"directory": directory}, "cost": 0.0123, "outcome": "succeeded",
         "metadata": metadata}
    if parent:
        s["parentID"] = parent
    s.update(extra)
    return s


def rows_of(sessions, busy=(), now=NOW):
    return {row["id"]: row for row in program_rows(list(sessions), set(busy), now)}


class ManagerAuthority(unittest.TestCase):
    def test_the_shared_manager_tuple_is_the_one_the_scripts_use(self):
        self.assertEqual(R.MANAGER_AGENTS, ("manager", "manager-gpt"))

    def test_a_root_manager_agent_is_genuine_with_null_missing_or_manager_metadata(self):
        self.assertTrue(R.is_manager_session({"id": "ses_a", "agent": "manager"}))
        self.assertTrue(R.is_manager_session({"id": "ses_a", "agent": "manager-gpt", "metadata": None}))
        self.assertTrue(R.is_manager_session({"id": "ses_a", "agent": "manager",
                                              "metadata": {"kind": "manager"}}))
        self.assertTrue(R.is_manager_session({"id": "ses_a", "agent": "manager",
                                              "metadata": {"kind": "manager",
                                                           "launch_id": "ses_caller",
                                                           "manager_session": "193"}}))

    def test_title_and_folder_alone_never_confer_manager_authority(self):
        self.assertFalse(R.is_manager_session({"id": "ses_a", "agent": "quick-ds41-flash",
                                               "title": "Manager-Session-180",
                                               "location": {"directory": "/x/manager-sessions"}}))
        self.assertFalse(R.is_manager_session({"id": "ses_a", "agent": "worker",
                                               "title": "Manager-Session-180"}))
        # A genuine Manager does not need a Manager-Session title; the actual agent is the authority.
        self.assertTrue(R.is_manager_session({"id": "ses_a", "agent": "manager", "title": "Untitled"}))

    def test_children_and_non_manager_agents_are_never_managers(self):
        self.assertFalse(R.is_manager_session({"id": "ses_child", "agent": "manager",
                                               "parentID": "ses_manager"}))
        self.assertFalse(R.is_manager_session({"id": "ses_a", "agent": "manager-quick"}))
        self.assertFalse(R.is_manager_session({"id": "ses_a", "agent": "conductor"}))
        self.assertFalse(R.is_manager_session(None))
        self.assertFalse(R.is_manager_session("ses_a"))
        self.assertFalse(R.is_manager_session({"id": "ses_a"}))

    def test_every_ownership_stamp_makes_a_manager_agent_non_manager(self):
        for stamp, value in (("quick", True), ("program", "[ap:ABC123]"), ("socket", "[ap:SOCK01]"),
                             ("parent_seat", "ses_lead000001"), ("seat_base", "sb-123456"),
                             ("warm_sid", "ses_warm000001"), ("fork", True),
                             ("forked_from", "ses_source0001"), ("conductor_sid", "ses_cond000001"),
                             ("conductor", "ses_cond000001"), ("conductor_n", 1),
                             ("seat_role", "worker")):
            with self.subTest(stamp=stamp):
                self.assertFalse(R.is_manager_session(
                    {"id": "ses_a", "agent": "manager", "metadata": {stamp: value}}))
        for kind in ("seat", "worker", "helper", "conductor", "warm", "fork", "subagent"):
            with self.subTest(kind=kind):
                self.assertFalse(R.is_manager_session(
                    {"id": "ses_a", "agent": "manager", "metadata": {"kind": kind}}))

    def test_malformed_metadata_is_not_manager_evidence(self):
        for malformed in ("not-a-mapping", [], 7, True):
            with self.subTest(metadata=malformed):
                self.assertFalse(R.is_manager_session(
                    {"id": "ses_a", "agent": "manager", "metadata": malformed}))


class ManagerRows(unittest.TestCase):
    def test_a_genuine_manager_is_a_manager_row_without_a_program(self):
        manager = session("ses_manager0001", title="Manager-Session-193", metadata=None)
        row = rows_of([manager], busy={"ses_manager0001"})["ses_manager0001"]
        self.assertEqual({key: row.get(key) for key in ("kind", "role", "backend", "manager")},
                         {"kind": "manager", "role": "Manager", "backend": "opencode", "manager": True})
        self.assertNotIn("program", row)
        self.assertNotIn("socket", row)
        self.assertNotIn("parentId", row)
        self.assertEqual((row["title"], row["agent"], row["directory"], row["busy"]),
                         ("Manager-Session-193", "manager",
                          "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions", True))
        self.assertEqual(row["model"], "openai/gpt-6.1-sol#high")
        self.assertEqual(row["createdAt"], NOW - 3600)       # seconds, like the Claude rows
        self.assertEqual(row["cost"], 0.0123)

    def test_manager_gpt_and_kind_manager_metadata_are_genuine_managers(self):
        rows = rows_of([
            session("ses_mgrgpt0001", agent="manager-gpt",
                    metadata={"kind": "manager", "manager_session": "194", "launch_id": "ses_caller"}),
            session("ses_mgrkind001", agent="manager", metadata={"kind": "manager"}),
        ])
        self.assertEqual(set(rows), {"ses_mgrgpt0001", "ses_mgrkind001"})
        self.assertTrue(all(row["kind"] == "manager" and row["manager"] is True for row in rows.values()))

    def test_projection_has_no_second_age_filter_for_managers_or_program_members(self):
        old = NOW - 200 * 86400
        rows = rows_of([
            session("ses_mgrold0001", title="Manager-Session-101", created=old),
            session("ses_condold001", title="[ap:OLDPRG]-001 · ancient", agent="sol", created=old),
        ])
        self.assertEqual(set(rows), {"ses_mgrold0001", "ses_condold001"})
        self.assertEqual(rows["ses_mgrold0001"]["kind"], "manager")

    def test_a_manager_title_or_folder_on_a_worker_is_not_listed_as_a_manager(self):
        rows = rows_of([
            session("ses_spoof00001", title="Manager-Session-180", agent="quick-ds41-flash"),
            session("ses_folder0001", title="Manager-Session-180", agent="quick-ds41-flash",
                    directory="/mnt/e/worker/manager-sessions"),
            session("ses_plain00001", title="Manager-Session-180", agent="quick-ds41-flash",
                    metadata={"kind": "seat"}),
        ])
        self.assertFalse(any(row.get('manager') or row.get('role') == 'Manager' for row in rows.values()))

    def test_seats_quick_helpers_children_and_stamped_agents_keep_their_old_rows(self):
        sessions = [
            session("ses_seat000001", agent="manager", title="[ap:ABC123]-001 ▸ worker · x",
                    metadata={"kind": "seat", "program": "[ap:ABC123]"}),
            session("ses_fsquick0001", agent="manager", title="▸ ⚡ quick worker · x",
                    metadata={"quick": True, "kind": "seat", "manager_session": "193"}),
            session("ses_warm0000001", agent="manager", title="▸ warm · parked",
                    metadata={"kind": "warm", "program": "[ap:ABC123]"}),
            session("ses_child000001", agent="manager", parent="ses_seat000001"),
            session("ses_badmd000001", agent="manager", metadata=["not", "a", "mapping"]),
        ]
        rows = rows_of(sessions)
        seat = rows["ses_seat000001"]
        self.assertEqual((seat["kind"], seat["program"], seat["manager"] if "manager" in seat else None),
                         ("seat", "ABC123", None))
        quick = rows["ses_fsquick0001"]
        self.assertEqual((quick["kind"], quick["quick"], quick["managerSession"],
                          quick.get("manager")), ("seat", True, "193", None))
        self.assertNotIn("program", quick)
        self.assertNotIn("ses_warm0000001", rows)
        self.assertNotIn("manager", rows["ses_badmd000001"])
        child = rows["ses_child000001"]
        self.assertEqual((child["kind"], child["parentId"], child.get("manager")),
                         ("subagent", "ses_seat000001", None))
        self.assertTrue(all(row.get("manager") is not True for row in rows.values()))

    def test_a_managers_subagent_child_joins_under_it_without_becoming_a_manager(self):
        manager = session("ses_manager0002", title="Manager-Session-195")
        child = session("ses_child000002", title="Explore (@general subagent)", agent="quick-ds41-flash",
                        parent="ses_manager0002")
        rows = rows_of([manager, child])
        self.assertEqual(set(rows), {"ses_manager0002", "ses_child000002"})
        self.assertEqual((rows["ses_child000002"]["kind"], rows["ses_child000002"]["parentId"]),
                         ("subagent", "ses_manager0002"))
        self.assertNotIn("manager", rows["ses_child000002"])

    def test_existing_program_and_quick_rows_keep_their_exact_shape(self):
        sessions = [
            session("ses_cond0000001", title="[ap:PGBMNF]-021 · Phrase Signal Lab", agent="sol"),
            session("ses_child000003", title="Build batch (@quick subagent)", agent="quick-ds41-flash",
                    parent="ses_cond0000001"),
            session("ses_helper00001", title="[ap:PGBMNF]-001 ▸ ⚡ quick helper · [ap:WB93JF]", agent="quick-ds41-flash",
                    metadata={"kind": "helper", "quick": True, "program": "[ap:PGBMNF]",
                              "parent_seat": "ses_lead000001", "conductor_sid": "ses_cond0000001",
                              "conductor_n": 1, "manager_session": "183"}),
        ]
        rows = rows_of(sessions, busy={"ses_helper00001"})
        self.assertEqual((rows["ses_cond0000001"]["kind"], rows["ses_cond0000001"]["program"],
                          rows["ses_cond0000001"]["conductorN"]), ("conductor", "PGBMNF", 21))
        self.assertEqual((rows["ses_child000003"]["kind"], rows["ses_child000003"]["parentId"]),
                         ("subagent", "ses_cond0000001"))
        helper = rows["ses_helper00001"]
        self.assertEqual((helper["kind"], helper["parentSeat"], helper["conductorId"], helper["conductorN"],
                          helper["managerSession"], helper["busy"]),
                         ("helper", "ses_lead000001", "ses_cond0000001", 1, "183", True))
        for sid in rows:
            self.assertNotIn("manager", rows[sid])
            self.assertNotIn("role", rows[sid])
            self.assertNotIn("backend", rows[sid])

    def test_manager_rows_are_not_duplicated_or_enriched_from_ownership_metadata(self):
        manager = session("ses_manager0003", title="Manager-Session-196",
                          metadata={"kind": "manager", "manager_session": "196", "launch_id": "ses_caller"})
        rows = program_rows([manager], set(), NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["kind"], row["role"], row["backend"], row["manager"]),
                         ("manager", "Manager", "opencode", True))
        self.assertNotIn("managerSession", row)      # this row is not "for" another Manager
        self.assertNotIn("conductorN", row)
        self.assertNotIn("seatRole", row)


if __name__ == "__main__":
    unittest.main()
