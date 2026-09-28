"""Main loop: observe -> plan -> (approve) -> execute with escalation -> record."""
import json
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from . import safety
from .actions import ActionResult
from .browser import Browser
from .config import Settings
from .executor import Executor
from .llm import LLM
from .memory import Memory, Step
from .observer import observe

console = Console()


class BrowserAgent:
    def __init__(self, task: str, settings: Settings | None = None):
        self.task = task
        self.settings = settings or Settings()
        self.browser = Browser(self.settings)
        self.llm = LLM(self.settings)
        self.memory = Memory()
        self._thought = ""
        self.executor = Executor(self.browser, self.llm, goal=lambda: f"{self.task} | now: {self._thought}")
        self.run_dir = self.settings.runs_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
        self._handoff_urls: set[str] = set()

    def _shot(self, name: str) -> Path:
        path = self.run_dir / f"{name}.png"
        try:
            self.browser.current().screenshot(path=str(path))
        except Exception:
            pass
        return path

    def _log(self, n: int, step: Step) -> None:
        with open(self.run_dir / "steps.jsonl", "a") as f:
            f.write(json.dumps({
                "step": n, "url": step.url, "plan": step.plan.model_dump(),
                "result": step.result.model_dump() if step.result else None, "note": step.note,
            }) + "\n")

    def run(self) -> str:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        page = self.browser.start()
        if page.url in ("about:blank", ""):
            page.goto(self.settings.start_url, wait_until="domcontentloaded")
        console.rule(f"[bold]Task: {self.task}")
        summary = "Stopped: step limit reached."
        try:
            for n in range(1, self.settings.max_steps + 1):
                page = self.browser.current()
                self.browser.settle()
                self.browser.dismiss_popups()

                # CAPTCHA / OTP: hand over to the human once per page
                reason = safety.needs_handoff(page)
                if reason and page.url not in self._handoff_urls:
                    self._handoff_urls.add(page.url)
                    note = safety.wait_for_human(reason)
                    if note:
                        self.memory.user_notes.append(note)
                    continue

                obs = observe(page)
                shot = self._shot(f"step{n:02d}")
                # Vision in planning only when the DOM gives little to work with, or we're failing
                use_image = len([e for e in obs.elements if e.in_view]) < 3 or self.memory.last_failed()
                try:
                    plan = self.llm.plan_next_action(
                        self.task, self.memory.to_prompt(), self.browser.tabs_prompt() + "\n\n" + obs.to_prompt(),
                        screenshot=shot.read_bytes() if use_image and shot.exists() else None,
                    )
                except Exception as e:  # malformed LLM reply / API hiccup: skip this turn, don't kill the run
                    console.print(f"[red]Planner error, retrying next step:[/] {str(e).splitlines()[0][:200]}")
                    continue
                self._thought = plan.thought
                a = plan.action
                console.print(f"\n[bold cyan]Step {n}[/] {plan.thought}")
                console.print(f"  → {a.type} #{a.element_id} {a.description} "
                              f"{('text=' + repr(a.text)) if a.text else ''}")
                step = Step(n=n, url=page.url, plan=plan)

                if a.type == "done":
                    summary = a.text or "Done."
                    step.note = "done"
                    self.memory.add(step)
                    self._log(n, step)
                    break

                if a.type == "ask_user":
                    answer = Prompt.ask(f"[bold magenta]Agent asks[/]: {a.text}")
                    step.note = f"user answered: {answer}"
                    self.memory.user_notes.append(f"Q: {a.text} A: {answer}")
                    self.memory.add(step)
                    self._log(n, step)
                    continue

                el = obs.by_id.get(a.element_id) if a.element_id is not None else None
                sensitive = safety.is_sensitive(plan, el)
                if sensitive:
                    approved, note = safety.request_approval(plan, el, page.url, str(shot))
                    if not approved:
                        if not note:
                            summary = "Stopped by user before a sensitive action."
                            step.note = "user rejected"
                            self.memory.add(step)
                            self._log(n, step)
                            break
                        step.note = f"user rejected, said: {note}"
                        self.memory.user_notes.append(note)
                        self.memory.add(step)
                        self._log(n, step)
                        continue

                try:
                    step.result = self.executor.execute(a, obs, single_shot=sensitive)
                except Exception as e:  # never let one bad step kill the run
                    step.result = ActionResult(ok=False, message=str(e)[:200], attempts=[f"crash: {e}"])
                if not step.result.ok:
                    console.print(f"  [red]✗ failed[/]: {'; '.join(step.result.attempts[-3:])}")
                self.memory.add(step)
                self._log(n, step)
        except KeyboardInterrupt:
            summary = "Interrupted by user."
        console.rule("[bold green]Result")
        console.print(summary)
        console.print(f"Run log: {self.run_dir}")
        if sys.stdin.isatty():
            Prompt.ask("Press Enter to close the browser", default="")
        self.browser.close()
        return summary
