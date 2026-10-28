# A1 offline evidence

Base: `f3aec099784b05ddcc3026b7a804bb0600f39927`.
Normalizer/schema commit: `81b1198`; adapter/tests commit: `f0a6aa3`.
No live pane, OpenCode, host, inference, restart, git reconstruction or source-file read was used for normalization.

## Repeatable command

For **each file separately**, from the worktree root:

```
HOME=$(mktemp -d /tmp/opencode/pane-offline.XXXXXX) python3 -m unittest discover -s transcription-pane/tests -p '<file>'
```

Final targeted run after restoring the mutation: all pass, **241 tests**:

| file | tests |
|---|---:|
| test_tool_diff.py | 10 |
| test_claude_reader.py | 69 |
| test_claude_live.py | 40 |
| test_opencode_conversation.py | 37 |
| test_opencode_conversation_api.py | 15 |
| test_phone_remote.py | 18 |
| test_tool_inspector.py | 50 |
| test_rows_golden.py | 2 |

## Fails-before checks

Saved the four production adapter changes as `tmp/a1-adapters.patch`, checked out
their original bytes (unchanged since the base), ran the six targeted files one
at a time with new tests retained, then restored with `git apply` in an EXIT trap.
No stash/reset. All new adapter assertions fail on pre-change owners:

| test method (file is the prefix above) | pre-change outcome |
|---|---|
| ReaderTests.test_snapshot_recorded_diff_is_separate_from_exact_raw_and_optional_failures | KeyError diff; all five edit/missing/malformed/pending/error subcases |
| LiveTests.test_host_recorded_diff_reaches_snapshot_and_mailbox_with_exact_raw | KeyError diff |
| LiveTests.test_host_malformed_optional_diff_does_not_lose_raw_snapshot | KeyError diff |
| SnapshotTests.test_direct_snapshot_normalizes_recorded_files_without_changing_raw_contract | KeyError diff; completed/pending/error subcases |
| ApiTests.test_recorded_diff_exact_equality_direct_phone_and_mailbox_preserves_raw_wire | KeyError diff |
| PhoneRemoteTests.test_phone_tool_read_reuses_exact_reader_snapshot_without_touching_desktop_mailbox (extended existing test) | KeyError diff |
| MailboxTests.test_mailbox_normalizes_diff_beside_part_and_keeps_optional_errors_local | KeyError diff |
| InspectorHTTPTests.test_final_mailbox_wire_bound_includes_diff_and_preserves_part_splice | FAIL: ToolError not raised at final wire bound |

The ten DiffTests methods (Claude edits/MultiEdit/update; creation/empty/newline;
OpenCode multi-file/hunks; rename/empty; write; missing/error/shell; malformed;
mixed counts; bad headers/counts; large diff) cannot run before the owner exists.
Verified by temporarily moving `tool_diff.py` into ignored scratch: the targeted
file fails to import with ModuleNotFoundError tool_diff, exit 1. This is absent-
schema evidence, **not** the semantic mutation evidence below. Source restored.

## Semantic mutation

At `f0a6aa3`, replaced only `OpenCodeConversation.snapshot`'s
`"diff": opencode_diff(p.get("name"), state)` with `"diff": None`.
Ran `test_opencode_conversation_api.py` using the command above:
15 tests, **one semantic assertion failure**, exit 1:

```
FAIL: test_recorded_diff_exact_equality_direct_phone_and_mailbox_preserves_raw_wire
AssertionError: None != {'files': [...], 'source': 'opencode-unified-patch', 'complete': True}
```

Restored the adapter, then reran every targeted file: 241/241 pass. Full logs
are ignored scratch `tmp/a1-{before-*,mutation-adapter,final-*}.log` in the
handover worktree. No mutation remains in production. JS snapshot differential,
row golden, provider-field omission and raw lexical wire assertions were not
weakened; the extension snapshot helper and rows-golden fixture are untouched.

## Fixture provenance / limits

`claude-history.json`: scrubbed actual history payload with recorded start 124;
create shape separately observed in a local permissions-probe transcript.
`opencode-state.json`: **constructed**, non-secret cases in the repository's
observed V2 envelope, with files[] fields checked against the local offline
OpenCode file-diff/edit/patch source. Not a purported capture of a live patch.
Host test envelope is inert `InertHost.frame` with the native `tool_use_result`
field. Aggregate MultiEdit shapes were not observed and are explicitly incomplete;
ordinary single-file structuredPatch is supported under MultiEdit identically.
Current OpenCode moves record destination `file`, not an old path; oldPath is
emitted only if that optional field is actually present, tested synthetically.
