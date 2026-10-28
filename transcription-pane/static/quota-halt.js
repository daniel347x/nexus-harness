/* A usage-limit halt at a glance (desktop /sessions, phone Programs and /m Chat). Since 2026-10-22 the Claude
   host ends the turn of a session that hits a Max usage limit instead of sleeping until the reset, and records
   `quota_halt` (status, rate_limit_type, resets_at, generation …) on it; the pane server carries it in each
   session's live state. This file only words it: one line for the conversation banner, the row marker's hover
   and the glance. The host clears the halt at the next accepted input (quota_halt becomes null; the old one moves
   to last_quota_halt, which is never shown), and a resumed or reconnected generation starts without one, so a
   halt naming another generation is old news.
   Since 2026-10-24 a five-hour halt also carries the host's one read-only recovery-check notice
   (`quota_recovery_check`: episode_id, resets_at, due_at, status, recipient, message_uuid, failure?, sent_at?)
   for the same episode reset. It is bookkeeping, not a session pending_recovery or hold: `active` projects it
   as `recoveryCheck` on this generation's halt and `line` appends its one line after the halt line. Nothing here
   reads, sends or blocks anything. */
(() => {
  'use strict';
  // Claude Code's rateLimitType values (the host's USAGE_LIMIT_TYPES), as Dan reads them.
  const WINDOWS = {five_hour: 'five-hour', seven_day: 'weekly', seven_day_opus: 'Opus weekly',
    seven_day_sonnet: 'Sonnet weekly', seven_day_overage_included: 'weekly', overage: 'overage'};
  function windowName(type) {
    if (typeof type !== 'string' || !type) return 'usage';
    if (Object.hasOwn(WINDOWS, type)) return WINDOWS[type];
    return type.replace(/^five_hour/, 'five-hour').replace(/^seven_day/, 'weekly').replace(/_/g, ' ');
  }
  // The host's recovery-check statuses; anything else is not shown rather than guessed.
   const CHECK_STATUSES = ['scheduled', 'preparing', 'sending', 'sent', 'delivery_problem', 'deferred_by_operator', 'cancelled_by_operator'];
  // This halt's own five-hour notice: the live state's `quota_recovery_check`, only when it names the same
  // episode reset (the host keys one episode by the structured five-hour reset) and a known status. Absent,
  // malformed or another reset's metadata is ignored; a launch-guard pause has no halt to attach to.
  function checkOf(live, halt) {
    const check = live && typeof live === 'object' ? live.quota_recovery_check : null;
    if (!halt || !check || typeof check !== 'object' || Array.isArray(check) || !CHECK_STATUSES.includes(check.status)) return null;
    const at = halt.resets_at, reset = check.resets_at;
    if (typeof at === 'number' && Number.isFinite(at) &&
        (typeof reset !== 'number' || !Number.isFinite(reset) || Math.abs(reset - at) >= 0.001))
      return null; // a known halt reset names one episode; missing or different metadata is not it
    return check;
  }
  // The live state's halt of this very generation, else null. An unknown generation on either side still shows it.
  // The halt object itself is never mutated: the returned one is a projection carrying `recoveryCheck`.
  function active(live, generation = null) {
    const halt = live && typeof live === 'object' ? live.quota_halt : null;
    if (!halt || typeof halt !== 'object' || Array.isArray(halt)) return null;
    if (Number.isSafeInteger(generation) && Number.isSafeInteger(halt.generation) && halt.generation !== generation) return null;
    const projected = {...halt}, check = checkOf(live, halt);
    if (check) projected.recoveryCheck = check; else delete projected.recoveryCheck;
    return projected;
  }
  // Local time HH:MM; a day other than today adds its weekday ("Tue 14:50").
  function clock(seconds, now) {
    const at = new Date(seconds * 1000), today = new Date(now), pad = n => String(n).padStart(2, '0');
    const time = pad(at.getHours()) + ':' + pad(at.getMinutes());
    return at.toDateString() === today.toDateString() ? time : at.toLocaleDateString('en-US', {weekday: 'short'}) + ' ' + time;
  }
  function resetText(halt, now) {
    const at = halt.resets_at;
    if (typeof at !== 'number' || !Number.isFinite(at) || at <= 0) return 'reset time unknown';
    return (at * 1000 <= now ? 'reset since ' : 'resets ') + clock(at, now);
  }
  // The notice's one line: due at its structured reset + five minutes (scheduled, preparing, sending), sent, or
  // delivery problem with the host's reason. It never claims a session resumes by itself.
  function recoveryLine(check, {now = Date.now()} = {}) {
    if (!check || typeof check !== 'object' || !CHECK_STATUSES.includes(check.status)) return '';
    if (['deferred_by_operator', 'cancelled_by_operator'].includes(check.status)) {
      const action = check.status === 'deferred_by_operator' ? 'deferred' : 'cancelled';
      return 'Manager recovery check ' + action + ' by operator — not armed' +
        (check.send_admitted ? '; a notice was already admitted (' + (check.delivery_status || check.receipt_status || 'pending') + '), never replayed' : '');
    }
    if (check.status === 'sent') return 'Manager recovery check sent';
    if (check.status === 'delivery_problem') {
      const failure = typeof check.failure === 'string' ? check.failure.replace(/\s+/g, ' ').trim().slice(0, 300) : '';
      return 'Manager recovery check delivery problem' + (failure ? ': ' + failure : '');
    }
    const due = check.due_at;
    return 'Manager recovery check due at ' + (typeof due === 'number' && Number.isFinite(due) && due > 0 ? clock(due, now) : 'time unknown');
  }
  // ⛔ Halted: Max usage limit reached (five-hour), resets 14:50. Then what Dan does: a Manager reconnects on his
  // next Send; anything else is resumed by its Conductor (or the Manager). A five-hour notice's own line follows.
  function line(halt, {manager = false, now = Date.now()} = {}) {
    if (!halt) return '';
    const check = recoveryLine(halt.recoveryCheck, {now});
    const controlled = ['deferred_by_operator', 'cancelled_by_operator'].includes(halt.recoveryCheck?.status);
    const advice = check && !controlled ? (manager ?
      'The host notifies the current Manager to coordinate recovery. You can also switch accounts and Send to reconnect.' :
      'Its Conductor (or the Manager) resumes it; no interrupted turn is replayed.') :
      (manager ? 'Nothing resumes by itself. Switch accounts or wait for the reset, then Send: it reconnects on the current login.' :
        'Nothing resumes by itself; its Conductor (or the Manager) resumes it.');
    const halted = '⛔ Halted: Max usage limit reached (' + windowName(halt.rate_limit_type) + '), ' + resetText(halt, now) + '. ' +
      advice;
    return halted + (check ? '\n' + check : '');
  }
  globalThis.opcQuotaHalt = Object.freeze({active, line, windowName, recoveryLine});
})();
