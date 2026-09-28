"""Turn the live page into a compact, numbered list of interactive elements for the LLM."""
from dataclasses import dataclass, field

from playwright.sync_api import Frame, Page

MAX_ELEMENTS = 150

# Runs inside each frame. Tags every candidate with data-agent-id and returns its description.
COLLECT_JS = r"""
(startId) => {
  document.querySelectorAll('[data-agent-id]').forEach(e => e.removeAttribute('data-agent-id'));
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, summary, label[for],' +
    '[role=button], [role=link], [role=option], [role=tab], [role=checkbox], [role=radio],' +
    '[role=menuitem], [role=combobox], [role=switch], [role=textbox], [role=searchbox],' +
    '[onclick], [tabindex]:not([tabindex="-1"]), [contenteditable=true]';
  const seen = new Set();
  const cands = [];
  const collect = (root) => {
    root.querySelectorAll(SEL).forEach(e => { if (!seen.has(e)) { seen.add(e); cands.push(e); } });
    // Custom widgets: divs/spans styled as clickable (seat maps, date cells, cards)
    root.querySelectorAll('div, span, li, td, img, svg').forEach(e => {
      if (seen.has(e)) return;
      if (getComputedStyle(e).cursor === 'pointer' &&
          (!e.parentElement || getComputedStyle(e.parentElement).cursor !== 'pointer')) {
        seen.add(e); cands.push(e);
      }
    });
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) collect(e.shadowRoot); });
  };
  collect(document);

  const vw = window.innerWidth, vh = window.innerHeight;
  const out = [];
  let id = startId;
  for (const e of cands) {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue;
    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
    let labelText = '';
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) labelText = l.innerText; }
    const name = clean(e.getAttribute('aria-label') || labelText || e.innerText || e.getAttribute('placeholder') ||
                       e.getAttribute('alt') || e.getAttribute('title') || e.getAttribute('name') ||
                       (e.tagName === 'INPUT' && ['submit','button'].includes(e.type) ? e.value : '')).slice(0, 80);
    const inView = r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
    let occluded = false;
    if (inView) {  // something else sits on top of this element's centre (hidden carousel slides, overlays)
      const cx = Math.min(Math.max(r.left + r.width / 2, 0), vw - 1), cy = Math.min(Math.max(r.top + r.height / 2, 0), vh - 1);
      const top = document.elementFromPoint(cx, cy);
      occluded = !!top && top !== e && !e.contains(top) && !top.contains(e);
    }
    e.setAttribute('data-agent-id', String(id));
    out.push({
      id, tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '',
      type: e.getAttribute('type') || '', name,
      placeholder: clean(e.getAttribute('placeholder')).slice(0, 40),
      value: ('value' in e && typeof e.value === 'string') ? e.value.slice(0, 40) : '',
      href: (e.getAttribute('href') || '').slice(0, 60),
      checked: !!e.checked, disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true',
      options: e.tagName === 'SELECT' ? Array.from(e.options).slice(0, 15).map(o => clean(o.text)) : [],
      x: r.left, y: r.top, w: r.width, h: r.height, in_view: inView, occluded,
    });
    id++;
  }
  return out;
}
"""

PAGE_TEXT_JS = "() => (document.body ? document.body.innerText : '').replace(/\\n{2,}/g, '\\n').slice(0, 4000)"


@dataclass
class Element:
    id: int
    frame: Frame
    tag: str
    role: str
    type: str
    name: str
    placeholder: str
    value: str
    href: str
    checked: bool
    disabled: bool
    options: list[str]
    x: float
    y: float
    w: float
    h: float
    in_view: bool
    occluded: bool = False

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def locator(self):
        return self.frame.locator(f'[data-agent-id="{self.id}"]').first

    def describe(self) -> str:
        kind = self.role or (f"{self.tag}[{self.type}]" if self.type else self.tag)
        parts = [f"[{self.id}] <{kind}>", f'"{self.name}"' if self.name else ""]
        if self.placeholder and self.placeholder != self.name:
            parts.append(f"placeholder={self.placeholder!r}")
        if self.value:
            parts.append(f"value={self.value!r}")
        if self.href and self.tag == "a":
            parts.append(f"href={self.href}")
        if self.options:
            parts.append(f"options={self.options}")
        if self.checked:
            parts.append("checked")
        if self.disabled:
            parts.append("DISABLED")
        if not self.in_view:
            parts.append("(offscreen)")
        return " ".join(p for p in parts if p)


@dataclass
class Observation:
    url: str
    title: str
    elements: list[Element]
    page_text: str
    by_id: dict[int, Element] = field(default_factory=dict)

    def __post_init__(self):
        self.by_id = {e.id: e for e in self.elements}

    def to_prompt(self) -> str:
        lines = "\n".join(e.describe() for e in self.elements) or "(no interactive elements found)"
        return (f"URL: {self.url}\nTITLE: {self.title}\n\nINTERACTIVE ELEMENTS:\n{lines}\n\n"
                f"VISIBLE PAGE TEXT (truncated):\n{self.page_text}")


def _frame_offset(frame: Frame) -> tuple[float, float]:
    """Viewport offset of an iframe, so element boxes are in main-page coordinates."""
    ox = oy = 0.0
    f = frame
    while f.parent_frame is not None:
        box = f.frame_element().bounding_box()
        if box is None:
            return float("nan"), float("nan")
        ox += box["x"]
        oy += box["y"]
        f = f.parent_frame
    return ox, oy


def observe(page: Page) -> Observation:
    elements: list[Element] = []
    next_id = 1
    for frame in page.frames:
        if frame.is_detached():
            continue
        try:
            ox, oy = (0.0, 0.0) if frame == page.main_frame else _frame_offset(frame)
            if ox != ox:  # NaN -> invisible frame
                continue
            raw = frame.evaluate(COLLECT_JS, next_id)
        except Exception:
            continue  # cross-origin or navigating frame
        for r in raw:
            r["x"] += ox
            r["y"] += oy
            elements.append(Element(frame=frame, **r))
        if raw:
            next_id = raw[-1]["id"] + 1

    # Prefer what's on screen; keep ids stable (they're already tagged in the DOM).
    elements.sort(key=lambda e: (not e.in_view, e.y))
    elements = sorted(elements[:MAX_ELEMENTS], key=lambda e: e.id)
    try:
        text = page.evaluate(PAGE_TEXT_JS)
    except Exception:
        text = ""
    return Observation(url=page.url, title=page.title(), elements=elements, page_text=text)
