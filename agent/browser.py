"""Browser lifecycle: persistent headful Chromium, tab tracking, dialogs and popup dismissal."""
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from .config import Settings

# Only cookie/consent banners are auto-dismissed. Other modals (seat count, location)
# may be part of the flow, so the planner decides about those.
DISMISS_TEXTS = [
    "Accept all", "Accept All", "Accept all cookies", "Accept All Cookies",
    "Accept cookies", "I agree", "Allow all", "Reject all", "Reject All",
]


class Browser:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.dialog_log: list[str] = []
        self._tab_stack: list[Page] = []

    def start(self) -> Page:
        self._pw = sync_playwright().start()
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        w, h = self.settings.viewport
        self.context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.profile_dir),
            headless=self.settings.headless,
            viewport={"width": w, "height": h},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context.on("page", self._on_new_page)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._wire(self.page)
        return self.page

    def _wire(self, page: Page) -> None:
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog) -> None:
        self.dialog_log.append(f"{dialog.type}: {dialog.message}")
        # Accept alerts/confirms so flows continue; beforeunload is accepted too.
        dialog.accept()

    def _on_new_page(self, page: Page) -> None:
        # Sites often open product pages / payment gateways in new tabs: follow them.
        self._wire(page)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        self._activate(page)

    def _activate(self, page: Page) -> None:
        if self.page is not None and self.page is not page and not self.page.is_closed():
            self._tab_stack.append(self.page)  # remember where we came from
        self.page = page
        try:
            page.bring_to_front()
        except Exception:
            pass

    def current(self) -> Page:
        # If the active tab was closed, fall back to the tab we came from, else the latest one.
        if self.page is None or self.page.is_closed():
            self.page = self._previous_tab() or self.context.pages[-1]
        return self.page

    # ------------------------------------------------------------------ tabs
    def tabs(self) -> list[Page]:
        return [p for p in self.context.pages if not p.is_closed()]

    def _previous_tab(self) -> Page | None:
        while self._tab_stack:
            p = self._tab_stack.pop()
            if not p.is_closed() and p is not self.page:
                return p
        return None

    def switch_to(self, index: int) -> Page:
        tabs = self.tabs()
        if not 0 <= index < len(tabs):
            raise IndexError(f"no tab {index}; open tabs: 0..{len(tabs) - 1}")
        self._activate(tabs[index])
        return self.page

    def close_current_tab(self) -> Page:
        """Close the active tab and return to the tab that opened it (or the previous one)."""
        page = self.current()
        if len(self.tabs()) <= 1:
            raise RuntimeError("only one tab open; refusing to close it")
        opener = page.opener()
        page.close()
        target = opener if opener and not opener.is_closed() else (self._previous_tab() or self.tabs()[-1])
        self._tab_stack = [p for p in self._tab_stack if p is not target and not p.is_closed()]
        self.page = None
        self._activate(target)
        return self.page

    def go_back(self) -> str:
        """Browser Back; if this tab has no history (opened via target=_blank), go to the previous tab."""
        page = self.current()
        before = page.url
        try:
            page.go_back(wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            pass
        if page.url != before:
            return "went back in history"
        if len(self.tabs()) > 1:
            self.close_current_tab()
            return f"tab had no history: closed it and returned to previous tab ({self.page.url[:80]})"
        raise RuntimeError("no history to go back to and no other tab open")

    def tabs_prompt(self) -> str:
        cur = self.current()
        lines = []
        for i, p in enumerate(self.tabs()):
            try:
                title = p.title()[:60]
            except Exception:
                title = ""
            mark = "  <-- CURRENT" if p is cur else ""
            lines.append(f"[{i}] {title} | {p.url[:90]}{mark}")
        return "OPEN TABS:\n" + "\n".join(lines)

    def settle(self, timeout: int = 5000) -> None:
        page = self.current()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout)
            page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass

    def dismiss_popups(self) -> list[str]:
        """Best-effort: close cookie banners and modals that block interaction."""
        page = self.current()
        closed = []
        for text in DISMISS_TEXTS:
            try:
                loc = page.get_by_role("button", name=text, exact=True)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=1500)
                    closed.append(text)
            except Exception:
                continue
        return closed

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()
