"""Observational job selection for the boom; no I/O, caches or program creation."""
from dataclasses import dataclass, field

from oc_running import CONDUCTOR_RE, Owner, RunningOwners, brief, metadata, program_handle
from oc_binding import row_session_id


def activity(session):
    value = (session.get("time") or {}).get("updated") or 0
    return value if isinstance(value, (int, float)) else 0


def links(session):
    md = metadata(session)
    return list(dict.fromkeys(p for p in (session.get("parentID"), md.get("parent_seat"),
                                         md.get("conductor_sid"), md.get("conductor"))
                              if isinstance(p, str) and p))


@dataclass
class Entry:
    key: tuple
    members: list = field(default_factory=list)
    leads: list = field(default_factory=list)
    conductors: list = field(default_factory=list)
    numbers: dict = field(default_factory=dict)
    label: str = ""
    updated: float = 0


# @beacon[
#   id=oc-view@ViewEntries,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Group all observed sessions before recent limiting; omit identified Manager conversations from default recent jobs but retain explicit selection.,
# ]
class ViewEntries:
    def __init__(self, sessions, roots=None):
        # Deterministic deduplication even if an inventory page repeats a SID.
        self.by_id = {}
        for s in sorted((s for s in sessions if isinstance(s, dict) and s.get("id")),
                        key=lambda s: (activity(s), repr(sorted(s.items())))):
            self.by_id[s["id"]] = s
        roots = {h: dict(f) for h, f in (roots or {}).items()}
        for s in self.by_id.values():
            md = metadata(s)
            handle = program_handle(md.get("program"))
            if handle:
                for p in (md.get("conductor_sid"), md.get("conductor")):
                    if isinstance(p, str) and p in self.by_id:
                        f = roots.setdefault(handle, {})
                        f["registry"] = list(dict.fromkeys([*f.get("registry", []), p]))
        self.owners = RunningOwners(list(self.by_id.values()), roots)
        self.keys = {}
        self.problems = {}
        self.groups = {}
        for sid in sorted(self.by_id):
            key = self.key(sid)
            if metadata(self.by_id[sid]).get("kind") == "warm":
                continue  # Known parked material is not a job; exact --sid remains available.
            e = self.groups.setdefault(key, Entry(key))
            e.members.append(self.by_id[sid])
        for e in self.groups.values():
            self.finish(e)
        self.recent = sorted(self.groups.values(), key=lambda e: (-e.updated, e.key))
        self.job_recent = [e for e in self.recent if not (
            e.key[0] == "manager" and self.owners.is_manager_conversation(e.key[1]))]

    def key(self, sid, path=()):
        if sid in path or len(path) >= 64:
            return ("session", sid)
        if sid in self.keys:
            return self.keys[sid]
        s = self.by_id.get(sid)
        if not s:
            return ("session", sid)
        owner = self.owners.owner(sid)
        md = metadata(s)
        if owner.kind == "program":
            key = ("program", owner.identity)
        else:
            parents = links(s)
            inherited = {self.key(p, (*path, sid)) for p in parents if p in self.by_id}
            if len(inherited) > 1:
                self.problems[sid] = "conflicting job/family links"
            direct_manager = owner.kind == "manager" and owner.identity in parents
            # Native Manager subagents are jobs, regardless of the agent's name.
            # Explicit helpers, recursive descendants and conflicting links aren't.
            direct = direct_manager and all(p == owner.identity for p in parents)
            fresh = not parents and owner.kind in ("manager", "manager-label") and bool(md.get("quick"))
            if (direct or fresh) and md.get("kind") != "helper" and not owner.problem:
                socket = program_handle(md.get("socket"))
                # A metadata stamp is not proof it denotes a particular native Manager SID.
                key = ("job", owner.kind, owner.identity, socket or sid)
            elif len(inherited) == 1 and not owner.problem:
                inherited_key = next(iter(inherited))
                # A helper linked only to the conversation has no identified job lead.
                # Keep it visible rather than hiding it with that conversation.
                key = ("session", sid) if inherited_key[0] == "manager" else inherited_key
            elif owner.kind == "manager" and sid == owner.identity:
                key = ("manager", sid)
            else:
                key = ("session", sid)
        self.keys[sid] = key
        return key

    def finish(self, e):
        e.updated = max(activity(s) for s in e.members)
        e.members.sort(key=lambda s: (-activity(s), s["id"]))
        if e.key[0] == "program":
            handle = e.key[1]
            facts = self.owners.roots.get(handle, {})
            rows = facts.get("registry_rows", [])
            numbered = {row_session_id(r): r.get("number") for r in rows if isinstance(r, dict)}
            bindings = set(facts.get("registry", []))
            # Explicit conductor linkage identifies renamed conductors without a history read.
            for s in e.members:
                for p in (metadata(s).get("conductor_sid"), metadata(s).get("conductor")):
                    if isinstance(p, str) and p in self.by_id and self.keys.get(p) == e.key:
                        bindings.add(p)
                        numbered.setdefault(p, metadata(s).get("conductor_n"))
            for s in e.members:
                md = metadata(s)
                match = CONDUCTOR_RE.match(str(s.get("title") or "")) if not s.get("parentID") else None
                if s["id"] in bindings or (not s.get("parentID") and md.get("kind") not in ("seat", "helper", "warm")
                                           and (match or s.get("agent") == "conductor")):
                    e.conductors.append(s)
                    n = numbered.get(s["id"]) or (match.group(2) if match else None)
                    try:
                        e.numbers[s["id"]] = int(n)
                    except (TypeError, ValueError):
                        e.numbers[s["id"]] = None
            e.conductors.sort(key=lambda s: (e.numbers[s["id"]] is None, e.numbers[s["id"]] or activity(s), s["id"]))
            # One shared header: RunningOwners.label owns the Program/Group grammar and the honesty
            # suffix, fed with the aggregate evidence (registry/metadata preferred to title).
            evidences = {self.owners.owner(s["id"]).evidence for s in e.members
                         if self.owners.owner(s["id"]).kind == "program" and self.owners.owner(s["id"]).identity == handle}
            evidence = "registry" if facts.get("validated") or "registry" in evidences else ("metadata" if "metadata" in evidences else "title")
            e.label = self.owners.label(Owner("program", handle, evidence))
        elif e.key[0] == "job":
            # Only direct roots are candidates for the job's own message summary.
            e.leads = [s for s in e.members if not any(self.keys.get(p) == e.key for p in links(s))]
            lead = (e.leads or e.members)[0]
            owner = self.owners.owner(lead["id"])
            e.label = "Quick job · " + brief(lead.get("title") or "untitled", 110)
            e.label += " · under " + self.owners.label(owner)
            if program_handle(metadata(lead).get("socket")):
                e.label += " · socket " + program_handle(metadata(lead)["socket"])
        elif e.key[0] == "manager":
            e.leads = [self.by_id[e.key[1]]]
            e.label = self.owners.label(self.owners.owner(e.key[1])) + " · conversation"
        else:
            e.leads = [self.by_id[e.key[1]]]
            owner = self.owners.owner(e.key[1])
            e.label = "Session · " + brief(e.leads[0].get("title") or "untitled", 110) + " (unclassified ownership)"
            if self.problems.get(e.key[1]):
                e.label += " · ! " + self.problems[e.key[1]]
            if metadata(e.leads[0]).get("kind") == "helper":
                e.label += " · helper, not an independent program"

    def workers(self, e, conductor=None):
        excluded = {s["id"] for s in e.conductors + e.leads}
        rows = [s for s in e.members if s["id"] not in excluded and metadata(s).get("kind") != "warm"]
        if conductor is None:
            return rows
        def beneath(s, path=()):
            if s["id"] in path or len(path) >= 64:
                return False
            parents = links(s)
            return conductor in parents or any(beneath(self.by_id[p], (*path, s["id"])) for p in parents if p in self.by_id)
        return [s for s in rows if beneath(s)]
