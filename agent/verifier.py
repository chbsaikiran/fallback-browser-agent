"""Detect whether an action actually changed anything on the page."""
import hashlib
from dataclasses import dataclass

from playwright.sync_api import Page

SNAPSHOT_JS = r"""
() => {
  const a = document.activeElement;
  const els = document.querySelectorAll('a,button,input,select,textarea,[role],[aria-expanded],[aria-selected],[aria-checked],[class]');
  let sig = '';
  // Class/aria/value state of interactive elements + visible text length captures most UI changes
  for (let i = 0; i < els.length && i < 4000; i++) {
    const e = els[i];
    sig += e.tagName + (e.className && e.className.baseVal === undefined ? e.className : '') +
      (e.getAttribute('aria-expanded') || '') + (e.getAttribute('aria-selected') || '') +
      (e.getAttribute('aria-checked') || '') + (e.value ?? '') + (e.checked ? '1' : '') + '|';
  }
  const text = document.body ? document.body.innerText : '';
  return {
    sig: sig + '#' + text.slice(0, 200000), textLen: text.length,
    active: a ? (a.tagName + (a.id || '') + (a.getAttribute('name') || '')) : '',
    scrollY: window.scrollY,
  };
}
"""


@dataclass
class PageState:
    url: str
    dom_hash: str
    text_len: int
    active: str
    scroll_y: float
    n_pages: int


def snapshot(page: Page) -> PageState:
    try:
        s = page.evaluate(SNAPSHOT_JS)
    except Exception:  # navigating mid-evaluate means something definitely happened
        s = {"sig": "navigating", "textLen": -1, "active": "", "scrollY": 0}
    return PageState(
        url=page.url,
        dom_hash=hashlib.md5(s["sig"].encode()).hexdigest(),
        text_len=s["textLen"],
        active=s["active"],
        scroll_y=s["scrollY"],
        n_pages=len(page.context.pages),
    )


def changed(before: PageState, after: PageState) -> bool:
    return (
        before.url != after.url
        or before.dom_hash != after.dom_hash
        or abs(before.text_len - after.text_len) > 0
        or before.n_pages != after.n_pages
        or before.active != after.active
    )


def input_has_value(locator, expected: str) -> bool:
    try:
        val = locator.input_value(timeout=1000)
    except Exception:
        try:
            val = locator.inner_text(timeout=1000)  # contenteditable
        except Exception:
            return False
    return expected.strip().lower() in (val or "").strip().lower()
