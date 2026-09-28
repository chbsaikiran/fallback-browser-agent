"""Human-in-the-loop gates: approval before money moves, hand-off for CAPTCHA / OTP."""
import re

from playwright.sync_api import Page
from rich.console import Console
from rich.prompt import Confirm, Prompt

from .actions import Plan
from .observer import Element

console = Console()

SENSITIVE = re.compile(
    r"\b(pay(\s+now|\s+securely|\s*₹|\s*rs|\s*\$)?|make\s+payment|proceed\s+to\s+pay(ment)?|place\s+(your\s+)?order|"
    r"confirm\s+(and\s+pay|booking|order|payment|purchase)|complete\s+(purchase|booking|order|payment)|"
    r"submit\s+payment|purchase\s+now|checkout\s+&\s+pay)\b",
    re.I,
)

HANDOFF = re.compile(
    r"captcha|verify you are (a )?human|are you a robot|i'?m not a robot|enter (the )?otp|one[- ]time password|"
    r"verification code|security check",
    re.I,
)


def is_sensitive(plan: Plan, el: Element | None) -> bool:
    if plan.action.type not in ("click", "press_key", "select"):
        return False
    target = " ".join(filter(None, [el.name if el else "", plan.action.description]))
    return plan.is_sensitive or bool(SENSITIVE.search(target))


def request_approval(plan: Plan, el: Element | None, url: str, screenshot_path: str) -> tuple[bool, str]:
    """Returns (approved, optional instruction if rejected)."""
    target = el.name if el and el.name else plan.action.description
    console.rule("[bold red]APPROVAL NEEDED")
    console.print(f"About to [bold]{plan.action.type}[/] → [bold yellow]{target}[/]")
    console.print(f"Page: {url}\nWhy: {plan.thought}\nScreenshot: {screenshot_path}")
    if Confirm.ask("Approve this action?", default=False):
        return True, ""
    note = Prompt.ask("Not approved. Instruction for the agent (blank = stop)", default="")
    return False, note


def needs_handoff(page: Page) -> str | None:
    """Detect CAPTCHA / OTP walls the agent must not try to solve itself."""
    try:
        for f in page.frames:
            if re.search(r"recaptcha|hcaptcha|challenges\.cloudflare|arkoselabs", f.url):
                return "CAPTCHA frame detected"
        text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 5000) : ''")
    except Exception:
        return None
    m = HANDOFF.search(text)
    return f"page mentions '{m.group(0)}'" if m else None


def wait_for_human(reason: str) -> str:
    console.rule("[bold cyan]YOUR TURN")
    console.print(f"{reason}. Please handle it in the browser window.")
    return Prompt.ask("Press Enter when done (or type a note for the agent)", default="")
