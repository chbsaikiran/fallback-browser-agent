"""Execute one Action with escalating strategies.

Tier 1: semantic DOM (the tagged element, role/label/text locators)
Tier 2: alternate DOM tricks (force click, JS click, dispatched events, keyboard, per-key typing, JS value set)
Tier 3: vision (Set-of-Marks screenshot -> LLM picks a box; raw screenshot -> LLM gives x,y)

After every attempt the page is re-checked; "no error" is not success unless something changed.
"""
import re
import time
from typing import Callable

from playwright.sync_api import Locator, Page
from rich.console import Console

from . import som
from .actions import Action, ActionResult
from .browser import Browser
from .llm import LLM
from .observer import Element, Observation, observe
from .verifier import changed, input_has_value, snapshot

console = Console()
T = 2500  # per-attempt timeout (ms)

DISPATCH_CLICK_JS = """e => {
  e.scrollIntoView({block: 'center'});
  const r = e.getBoundingClientRect();
  const o = {bubbles: true, cancelable: true, view: window, clientX: r.left + r.width/2, clientY: r.top + r.height/2};
  for (const t of ['pointerover','pointerdown','mousedown','pointerup','mouseup','click'])
    e.dispatchEvent(new (t.startsWith('pointer') ? PointerEvent : MouseEvent)(t, o));
}"""

JS_SET_VALUE_JS = """(e, v) => {
  e.focus();
  const proto = e instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (e.isContentEditable) e.innerText = v; else if (setter) setter.call(e, v); else e.value = v;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
}"""

ACTIVE_VALUE_JS = "() => { const a = document.activeElement; return a ? (a.value ?? a.innerText ?? '') : ''; }"


def _quoted(description: str) -> str | None:
    m = re.search(r"['\"‘“]([^'\"’”]{1,80})['\"’”]", description or "")
    return m.group(1) if m else None


class Executor:
    def __init__(self, browser: Browser, llm: LLM | None, goal: Callable[[], str] = lambda: ""):
        self.single_shot = False
        self.browser = browser
        self.llm = llm
        self.goal = goal

    @property
    def page(self) -> Page:
        return self.browser.current()

    # ------------------------------------------------------------------ dispatch
    def execute(self, action: Action, obs: Observation, single_shot: bool = False) -> ActionResult:
        """single_shot: stop after the first attempt that doesn't error (used for pay/confirm clicks,
        which must never be fired twice)."""
        self.single_shot = single_shot
        el = obs.by_id.get(action.element_id) if action.element_id is not None else None
        handler = {
            "click": self._click, "type": self._type, "select": self._select,
        }.get(action.type)
        if handler:
            return handler(action, el)
        return self._simple(action)

    def _simple(self, a: Action) -> ActionResult:
        p = self.page
        msg = ""
        try:
            if a.type == "navigate":
                url = a.text if (a.text or "").startswith("http") else f"https://{a.text}"
                p.goto(url, wait_until="domcontentloaded", timeout=30_000)
            elif a.type == "press_key":
                p.keyboard.press(a.text or "Enter")
            elif a.type == "scroll":
                p.mouse.wheel(0, 700 if a.direction == "down" else -700)
            elif a.type == "go_back":
                msg = self.browser.go_back()
            elif a.type == "switch_tab":
                self.browser.switch_to(int(str(a.text).strip().lstrip("[").rstrip("]")))
                msg = f"switched to tab {a.text}"
            elif a.type == "close_tab":
                self.browser.close_current_tab()
                msg = f"closed tab, now on {self.page.url[:80]}"
            elif a.type == "wait":
                time.sleep(2.5)
            self.browser.settle()
            return ActionResult(ok=True, tier="direct", message=msg or a.type)
        except Exception as e:
            return ActionResult(ok=False, message=f"{a.type} failed: {e}")

    # ------------------------------------------------------------------ helpers
    def _semantic_locators(self, el: Element | None, a: Action) -> list[tuple[str, Locator]]:
        """Tier-1 candidates, most specific first."""
        p = self.page
        name = (el.name if el and el.name else None) or _quoted(a.description)
        out: list[tuple[str, Locator]] = []
        if el is not None:
            out.append(("t1:id", el.locator()))
        if name:
            if a.type == "click":
                for role in ("button", "link", "tab", "option", "menuitem", "radio", "checkbox"):
                    out.append((f"t1:role:{role}", p.get_by_role(role, name=name)))
                out.append(("t1:text", p.get_by_text(name, exact=False)))
            else:
                out.append(("t1:label", p.get_by_label(name)))
                out.append(("t1:placeholder", p.get_by_placeholder(name)))
                out.append(("t1:role:textbox", p.get_by_role("textbox", name=name)))
                out.append(("t1:role:combobox", p.get_by_role("combobox", name=name)))
        return out

    @staticmethod
    def _first_visible(loc: Locator) -> Locator | None:
        try:
            n = loc.count()
        except Exception:
            return None
        for i in range(min(n, 5)):
            cand = loc.nth(i)
            try:
                if cand.is_visible():
                    return cand
            except Exception:
                continue
        return None

    def _wait_ui(self) -> None:
        time.sleep(0.6)
        self.browser.settle(timeout=3000)

    def _vision_element(self, a: Action) -> tuple[Element | None, Observation]:
        """Tier 3a: Set-of-Marks. Returns the element the LLM picked."""
        obs = observe(self.page)
        if not self.llm:
            return None, obs
        png, ids = som.marked_screenshot(self.page, obs)
        if not ids:
            return None, obs
        pick = self.llm.pick_mark(png, a.description or a.text or "", self.goal(), ids)
        console.log(f"[magenta]vision/SoM picked[/] {pick}")
        return (obs.by_id.get(pick) if pick is not None else None), obs

    def _vision_coords(self, a: Action) -> tuple[int, int] | None:
        """Tier 3b: raw screenshot -> coordinates."""
        if not self.llm:
            return None
        vp = self.page.viewport_size or {"width": 1280, "height": 860}
        png = self.page.screenshot(type="png")
        xy = self.llm.locate_coordinates(png, a.description or a.text or "", self.goal(),
                                         vp["width"], vp["height"])
        console.log(f"[magenta]vision/coords[/] {xy}")
        return xy

    def _run(self, attempts: list[tuple[str, Callable[[], bool | None]]]) -> ActionResult:
        """Try strategies in order. A strategy returns False/raises on failure; otherwise the page must change."""
        log: list[str] = []
        for name, fn in attempts:
            before = snapshot(self.page)
            try:
                res = fn()
            except Exception as e:
                log.append(f"{name}: error {str(e).splitlines()[0][:120]}")
                continue
            if res is False:
                log.append(f"{name}: not applicable")
                continue
            self._wait_ui()
            ok = res is True or changed(before, snapshot(self.page))
            if not ok:  # slow UIs: give it one more moment before retrying with another strategy
                time.sleep(1.2)
                ok = changed(before, snapshot(self.page))
            if not ok and self.single_shot:
                log.append(f"{name}: fired, no visible change (not retrying: sensitive action)")
                return ActionResult(ok=True, tier=name, message="unverified", attempts=log)
            log.append(f"{name}: {'ok' if ok else 'no visible change'}")
            if ok:
                console.log(f"[green]✓[/] {name}")
                return ActionResult(ok=True, tier=name, attempts=log)
            console.log(f"[yellow]…[/] {name} had no effect, escalating")
        return ActionResult(ok=False, message="all strategies failed", attempts=log)

    # ------------------------------------------------------------------ click
    def _click(self, a: Action, el: Element | None) -> ActionResult:
        attempts: list[tuple[str, Callable]] = []

        # Tier 1
        for name, loc in self._semantic_locators(el, a):
            def t1(loc=loc):
                target = self._first_visible(loc)
                if target is None:
                    return False
                target.click(timeout=T)
            attempts.append((name, t1))

        # Tier 2: on the tagged element (or best semantic match)
        def target() -> Locator:
            for _, loc in self._semantic_locators(el, a):
                t = self._first_visible(loc)
                if t is not None:
                    return t
            raise LookupError("no element for tier 2")

        def force():
            t = target()
            t.scroll_into_view_if_needed(timeout=T)
            t.click(timeout=T, force=True)

        def mouse_center():
            box = target().bounding_box()
            if not box:
                return False
            self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

        def keyboard():
            t = target()
            t.focus(timeout=T)
            self.page.keyboard.press("Enter")

        attempts += [
            ("t2:force_click", force),
            ("t2:js_click", lambda: target().evaluate("e => e.click()")),
            ("t2:dispatch_events", lambda: target().evaluate(DISPATCH_CLICK_JS)),
            ("t2:mouse_at_center", mouse_center),
            ("t2:focus_enter", keyboard),
        ]

        # Tier 3
        def som_click():
            picked, _ = self._vision_element(a)
            if picked is None:
                return False
            try:
                picked.locator().click(timeout=T)
            except Exception:
                x, y = picked.center
                self.page.mouse.click(x, y)

        def coords_click():
            xy = self._vision_coords(a)
            if not xy:
                return False
            self.page.mouse.click(*xy)

        attempts += [("t3:som", som_click), ("t3:coords", coords_click)]
        return self._run(attempts)

    # ------------------------------------------------------------------ type
    def _type(self, a: Action, el: Element | None) -> ActionResult:
        text = a.text or ""
        state: dict = {}

        def resolve() -> Locator:
            if "loc" in state:
                return state["loc"]
            for _, loc in self._semantic_locators(el, a):
                t = self._first_visible(loc)
                if t is not None:
                    state["loc"] = t
                    return t
            raise LookupError("no input found")

        def verify_locator() -> bool:
            if input_has_value(resolve(), text):
                return True
            raise AssertionError("value did not stick")

        def fill():
            resolve().fill(text, timeout=T)
            return verify_locator()

        def per_key():
            t = resolve()
            t.click(timeout=T, force=True)
            self.page.keyboard.press("ControlOrMeta+A")
            self.page.keyboard.press("Backspace")
            t.press_sequentially(text, delay=60, timeout=T + 60 * len(text))
            return verify_locator()

        def js_set():
            resolve().evaluate(JS_SET_VALUE_JS, text)
            return verify_locator()

        def type_into_focused():
            self.page.keyboard.press("ControlOrMeta+A")
            self.page.keyboard.type(text, delay=50)
            if text.lower() not in str(self.page.evaluate(ACTIVE_VALUE_JS)).lower():
                raise AssertionError("focused element did not take the text")
            return True

        def som_type():
            picked, _ = self._vision_element(a)
            if picked is None:
                return False
            state["loc"] = picked.locator()
            picked.locator().click(timeout=T, force=True)
            return type_into_focused()

        def coords_type():
            xy = self._vision_coords(a)
            if not xy:
                return False
            self.page.mouse.click(*xy)
            return type_into_focused()

        res = self._run([
            ("t1:fill", fill), ("t2:per_key_typing", per_key), ("t2:js_set_value", js_set),
            ("t3:som", som_type), ("t3:coords", coords_type),
        ])
        if res.ok and a.submit:
            time.sleep(0.4)  # let autocomplete settle
            self.page.keyboard.press("Enter")
            self.browser.settle()
        return res

    # ------------------------------------------------------------------ select
    def _select(self, a: Action, el: Element | None) -> ActionResult:
        option = a.text or ""

        def native():
            if el is None or el.tag != "select":
                return False
            try:
                el.locator().select_option(label=option, timeout=T)
            except Exception:
                el.locator().select_option(value=option, timeout=T)
            return True

        def open_then_pick():
            if el is not None:
                el.locator().click(timeout=T)
            time.sleep(0.4)
            for loc in (self.page.get_by_role("option", name=option),
                        self.page.get_by_role("menuitem", name=option),
                        self.page.get_by_text(option, exact=True),
                        self.page.get_by_text(option)):
                t = self._first_visible(loc)
                if t is not None:
                    t.click(timeout=T)
                    return None
            return False

        def som_pick():
            opt = Action(type="click", description=f"option '{option}' in the open dropdown ({a.description})")
            picked, _ = self._vision_element(opt)
            if picked is None:
                return False
            picked.locator().click(timeout=T, force=True)

        return self._run([("t1:select_option", native), ("t2:open_and_click", open_then_pick),
                          ("t3:som", som_pick)])
