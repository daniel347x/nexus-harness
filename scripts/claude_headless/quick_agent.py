"""Launch-local native manager-quick, generated from the ONE quick-worker doctrine."""
import hashlib
from pathlib import Path

SOURCE = Path('/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker/AGENTS.md')

PREAMBLE = """# You were started by the Manager in native Claude Code

You are a **QUICK WORKER** started directly with the Manager's **Agent** tool, not a
Conductor or an OpenCode session. The following differences override the doctrine below:

1. **Your task is the prompt the Manager gives you.** Normally it names your socket:
   read that note in full first; it is your prompt. Write `## Result` there, then return
   a short completion to the Manager. With no socket, your final reply is your result.
2. **Your working directory (`manager-sessions`) is NOT your project.** Never write
   there. Give every command and file an absolute path in the repository your task names.
3. **You have no OpenCode `ses_…` id and no `cc-…` id of your own.** Claude Code subagents
   cannot start subagents. If helpers are authorized, start quick helpers, top-level sessions on
   OpenCode (`quick-ds41-flash`) or on Claude Code on Dan's Max (`quick-opus55-‹level›` ·
   `quick-fable51-‹level›`):
   `bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh seat quick --helper --agent quick-ds41-flash --socket "[ap:HELPER]" --no-wait`.
   Copy the id it prints (`ses_…` or `cc-…`) and collect with `oc.sh seat wait ‹id› --max 540`
   (2 means still running; wait again; for a `cc-…` helper 4 = needs attention and 5 = UNKNOWN:
   read what it printed, never treat either as done). Never pass a native agent id or this
   conversation's UUID to OpenCode resolvers or `--parent-seat`. You may simply do the bounded
   task without helpers.
4. **Tools are inherited.** Use native Bash/Read/Edit/Write/Grep/Glob and the inherited
   Workflowy MCP tools, named `mcp__workflowy__node`, `mcp__workflowy__anchor`, etc.,
   with the same `{verb, params}` grammar. Missing Workflowy tools: report and stop.
   Native Read pages with offsets; saved/truncated output is not proof of whole delivery.
5. **Context belongs to you, not the Manager.** The inherited `mcp__context__context_status`
   callback is bound to the PRIMARY session: do NOT call it or claim its reading is yours.
   Your own tool-time occupancy is unavailable. Keep work bounded, land a PARTIAL early
   if needed, and return to the Manager instead of running to a guessed window limit.
   This Manager-local route is not a Claude Toolkit seat (those are top-level sessions that
   the `oc_*` scripts start, each with its own measured context).
---

"""


# Thinking-effort variants of the same agent. The agent-definition schema (as read on 2.1.299;
# no version gate here) accepts effort as low|medium|high|xhigh|max or an integer; the CLI
# silently downgrades a level the chosen model does not support (as for --effort).
EFFORT_VARIANTS = ('medium', 'high', 'xhigh', 'max')


# @beacon[
#   id=claude-headless@manager_quick_definition,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Generates launch-local native manager-quick plus its -medium/-high/-xhigh/-max effort variants from the one quick-worker doctrine; inherited model and MCP tools, no native grandchildren.,
# ]
def manager_quick_definition() -> tuple[dict, dict]:
    raw = SOURCE.read_bytes()
    body = raw.decode('utf-8')
    if not body.strip():
        raise ValueError('quick-worker doctrine is empty; Manager launch refused')
    # Omit tools deliberately: a built-in-only list would cut off Workflowy MCP.
    base = {
        'description': 'Manager-controlled quick work on a socket; read its prompt and write ## Result. Use this agent, never general-purpose, for real quick work. Thinking effort: the session default.',
        'prompt': PREAMBLE + body,
        'model': 'inherit',
        # No native grandchildren, and the Manager's SendMessage (its own steering of these children)
        # stays the Manager's; MCP inheritance remains intact.
        'disallowedTools': ['Agent', 'SendMessage'],
    }
    definition = {'manager-quick': base}
    for level in EFFORT_VARIANTS:
        definition['manager-quick-' + level] = {**base, 'effort': level,
            'description': base['description'].replace('Thinking effort: the session default.',
                                                       f'Thinking effort: {level}.')}
    return definition, {'source_path': str(SOURCE),
        'source_sha256': hashlib.sha256(raw).hexdigest(), 'model': 'inherit',
        'effort_variants': ['manager-quick-' + level for level in EFFORT_VARIANTS],
        'tools': 'inherited shell/file and MCP; Agent and SendMessage excluded'}
