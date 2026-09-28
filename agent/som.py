"""Set-of-Marks: draw numbered boxes over interactive elements, then screenshot the viewport."""
from playwright.sync_api import Page

from .observer import Observation

DRAW_JS = r"""
(marks) => {
  const root = document.createElement('div');
  root.id = '__agent_som__';
  root.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;';
  const colors = ['#e6194b','#3cb44b','#4363d8','#f58231','#911eb4','#008080','#9a6324','#800000'];
  for (const m of marks) {
    const c = colors[m.id % colors.length];
    const box = document.createElement('div');
    box.style.cssText = `position:absolute;left:${m.x}px;top:${m.y}px;width:${m.w}px;height:${m.h}px;` +
      `border:2px solid ${c};box-sizing:border-box;`;
    const tag = document.createElement('div');
    tag.textContent = m.id;
    tag.style.cssText = `position:absolute;left:${m.x}px;top:${Math.max(0, m.y - 14)}px;background:${c};` +
      'color:#fff;font:bold 11px/14px monospace;padding:0 3px;';
    root.append(box, tag);
  }
  document.documentElement.appendChild(root);
}
"""
REMOVE_JS = "() => document.getElementById('__agent_som__')?.remove()"


def marked_screenshot(page: Page, obs: Observation) -> tuple[bytes, list[int]]:
    """Returns (png bytes, ids that were drawn)."""
    marks = [
        {"id": e.id, "x": e.x, "y": e.y, "w": e.w, "h": e.h}
        for e in obs.elements if e.in_view and not e.disabled and not e.occluded
    ]
    page.evaluate(DRAW_JS, marks)
    try:
        png = page.screenshot(type="png")
    finally:
        page.evaluate(REMOVE_JS)
    return png, [m["id"] for m in marks]
