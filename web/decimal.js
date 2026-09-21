/**
 * One decimal separator, one place that decides it.
 *
 * Every form in this tool used `type="number"`. A browser set to a comma
 * locale reads `0,8` in one of those as the EMPTY STRING -- so the figure an
 * engineer typed was dropped on the keystroke, silently, and the field fought
 * back. The fields are text with a decimal keypad instead, and these two
 * functions are the whole contract: tolerant coming in, a dot going out
 * (ADR-083).
 *
 * When the decimal separator is settled across the tool, this file is where
 * it changes -- not in eleven page scripts.
 */

/**
 * A number out of a form field, written with either separator.
 *
 * Returns null for "nothing here", which is not the same as zero: an empty
 * rack position follows the typical row, a zero is a cabinet with nothing in
 * it, and a form that confuses the two writes the wrong case.
 */
export const num = (text) => {
  const clean = String(text ?? '').trim().replace(',', '.');
  if (clean === '' || !Number.isFinite(Number(clean))) return null;
  return Number(clean);
};

/** What goes back into a field: never a comma, never a trailing zero. */
export const dec = (v) =>
  v === null || v === undefined || v === '' || !Number.isFinite(+v)
    ? '' : String(+v);

/** The attributes a decimal field carries, so every form spells them alike. */
export const DECIMAL_FIELD = 'type="text" inputmode="decimal"';
