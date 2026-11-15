export type Tone = "good" | "bad" | "warn" | "neutral";


/**
 * Pluralize a count with a word.
 *
 * Spec:
 * - pluralize(1, "monitor")  -> "1 monitor"
 * - pluralize(0, "monitor")  -> "0 monitors"
 * - pluralize(3, "monitor")  -> "3 monitors"
 * - Just add "s". Nothing here needs irregular plurals.
 */
export function pluralize(count: number, word: string): string {
  if (count === 1) {
    return `${count} ${word}`;
  }
  return `${count} ${word}s`;
}

/**
 * Format an uptime percentage.
 *
 * Spec:
 * - null -> "—"  (an em dash; the monitor has no checks yet)
 * - Clamp the value into the range 0 to 100 first.
 * - Use value.toFixed(2), then drop trailing zeros:
 *     100    -> "100%"
 *     99.5   -> "99.5%"
 *     99.95  -> "99.95%"
 *     0      -> "0%"
 * - HONESTY RULE: if the value is below 100 but toFixed(2) would show
 *   "100.00" (for example 99.999), show "99.99%" instead. Never display
 *   100% unless it really is 100.
 * - Don't use Math.floor(value * 100) / 100 to do this. Floating point
 *   makes 0.29 * 100 equal 28.999999999999996, which floors to the wrong
 *   number. toFixed avoids the trap.
 */
export function formatUptime(uptime: number | null): string {
  if (uptime === null) return "—";
  const clamped = Math.max(0, Math.min(100, uptime));
  const rounded = clamped.toFixed(2);
  if (clamped < 100 && rounded === "100.00") {
    return "99.99%";
  }
  return `${Number(rounded).toString()}%`;
}


/**
 * Format a response time.
 *
 * Spec:
 * - null -> "—"
 * - Round to a whole number of milliseconds first.
 * - Under 1000 ms -> "142 ms"
 * - 1000 ms or more -> seconds with one decimal: 1200 -> "1.2 s",
 *   5000 -> "5.0 s".
 * - 999.6 rounds to 1000, so it must come out as "1.0 s", not "1000 ms".
 */
export function formatResponseTime(value: number | null): string {
  if (value === null) return "—";
  const rounded = Math.round(value);
  if (rounded < 1000) {
    return `${rounded} ms`;
  }
  return `${(rounded / 1000).toFixed(1)} s`;
}

/**
 * Format an ISO timestamp in the viewer's local timezone.
 * Invalid or missing timestamps should not break the status page.
 */
export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
