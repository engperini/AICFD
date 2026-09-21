/**
 * Which case a page is looking at, and keeping it on every hop.
 *
 * ADR-087 made the API calls carry the case, so the server stopped answering
 * with whatever it was started on. It did not make the NAVIGATION carry it,
 * and the case lives in `location.search` -- so every link that dropped it
 * put the reader back on the server's start case without saying so.
 *
 * What that looked like, with `aicfd view --case pod-fanwall` serving and
 * `hall-10mw` open on the model page:
 *
 *   click Racks     the racks page loaded POD-FANWALL's racks and said so in
 *                   a header nobody reads twice. Editing there, and saving,
 *                   wrote the wrong case -- the worst kind of fault, because
 *                   the page did exactly what it said and the reader was
 *                   looking at the wrong room
 *   click back      the model page came back on pod-fanwall
 *
 * A back link is not a special case of this; it is the same hop the other
 * way. So the rule is one rule, applied to every link out of every page: a
 * link to a page of this tool carries the case unless it names one of its
 * own. New page, new link, same behaviour -- which is the point of putting it
 * here rather than in five page scripts (the mistake `decimal.js` was made to
 * fix, for the decimal separator).
 */

/** The case this page is looking at, or null where the URL names none. */
export const CASE = new URLSearchParams(location.search).get('case');

/** A URL with the case on it: for a fetch, or for a link built in a template. */
export const withCase = (url) => {
  if (!CASE || /[?&]case=/.test(url)) return url;
  return `${url}${url.includes('?') ? '&' : '?'}case=${encodeURIComponent(CASE)}`;
};

/**
 * Does this link go to a page of this tool?
 *
 * Same origin, same folder, and a real navigation -- not a `#` jump, not a
 * download, not a new window the case would be meaningless in.
 */
const ours = (a) => {
  if (!a || a.target === '_blank' || a.hasAttribute('download')) return false;
  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#') || /^[a-z]+:/i.test(href)) return false;
  try {
    const url = new URL(a.href, location.href);
    return url.origin === location.origin && url.pathname.startsWith('/web/');
  } catch {
    return false;
  }
};

/** Put the case on one anchor, leaving one that names its own alone. */
const carry = (a) => {
  if (!CASE || !ours(a)) return;
  const url = new URL(a.href, location.href);
  if (url.searchParams.has('case')) return;
  url.searchParams.set('case', CASE);
  a.href = url.href;
};

/**
 * Two passes, because the links arrive at two different times.
 *
 * The back link is in the HTML and is there before this module runs. The
 * links out of the model page are built by a template every time the model
 * re-renders, so they are new elements that no earlier pass can have seen --
 * hence the listener, in the capture phase, which also gets the
 * ctrl-click and middle-click that open a page in a new tab.
 *
 * It rewrites `href` rather than intercepting the navigation, so a case
 * arrives in the address bar of the new tab too, and the browser's own
 * "open in new tab" and "copy link" give a link that still names the case.
 */
if (CASE) {
  document.querySelectorAll('a[href]').forEach(carry);
  for (const event of ['click', 'auxclick', 'contextmenu']) {
    document.addEventListener(event, (e) => {
      const a = e.target instanceof Element ? e.target.closest('a[href]') : null;
      if (a) carry(a);
    }, true);
  }
}
