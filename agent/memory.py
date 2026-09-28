"""Step history for the planner prompt, plus stuck-loop detection."""
from collections import Counter
from dataclasses import dataclass, field

from .actions import ActionResult, Plan


@dataclass
class Step:
    n: int
    url: str
    plan: Plan
    result: ActionResult | None = None
    note: str = ""

    def key(self) -> tuple:
        a = self.plan.action
        return (self.url, a.type, a.element_id, a.description.lower()[:40], a.text)

    def line(self) -> str:
        a = self.plan.action
        what = f"{a.type}"
        if a.element_id is not None:
            what += f" #{a.element_id}"
        if a.description:
            what += f" ({a.description})"
        if a.text:
            what += f" text={a.text!r}"
        if self.result is None:
            outcome = self.note or "-"
        elif self.result.ok:
            outcome = f"OK via {self.result.tier}" + (f" [{self.result.message}]" if self.result.message else "")
        else:
            outcome = "FAILED after: " + "; ".join(self.result.attempts[-4:])
        extra = f" | note: {self.note}" if self.note and self.result is not None else ""
        thought = f" | thought: {self.plan.thought[:120]}" if self.plan.thought else ""
        return f"{self.n}. [{self.url[:70]}] {what} -> {outcome}{extra}{thought}"


@dataclass
class Memory:
    steps: list[Step] = field(default_factory=list)
    user_notes: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def add(self, step: Step) -> None:
        self.steps.append(step)
        fact = step.plan.remember.strip()
        if fact and fact not in self.findings:
            self.findings.append(fact)

    def to_prompt(self, last: int = 15) -> str:
        lines = []
        if self.findings:
            lines.append("FINDINGS SO FAR (already done, do not redo):")
            lines += [f"- {f}" for f in self.findings]
            lines.append("")
        lines += [s.line() for s in self.steps[-last:]]
        if self.user_notes:
            lines.append("USER SAID: " + " | ".join(self.user_notes))
        if warn := self.stuck_warning():
            lines.append(warn)
        return "\n".join(lines)

    def stuck_warning(self) -> str | None:
        recent = self.steps[-6:]
        counts = Counter(s.key() for s in recent)
        if counts and counts.most_common(1)[0][1] >= 3:
            return ("WARNING: you have repeated the same action 3+ times without progress. "
                    "Choose a DIFFERENT approach (other element, scroll, close popup, other path, or ask_user).")
        return None

    def last_failed(self) -> bool:
        return bool(self.steps) and self.steps[-1].result is not None and not self.steps[-1].result.ok
