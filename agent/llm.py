"""OpenAI wrapper: action planning from the element list, and screenshot-based element finding."""
import base64
import json
import re
from pathlib import Path

from openai import OpenAI
from pydantic import ValidationError

from .actions import Plan
from .config import Settings

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def _load_vision_prompts() -> dict[str, str]:
    text = (PROMPTS / "vision.md").read_text()
    sections = re.split(r"^## (\w+)\s*$", text, flags=re.M)
    return {sections[i]: sections[i + 1].strip() for i in range(1, len(sections), 2)}


def _image_part(png: bytes) -> dict:
    b64 = base64.b64encode(png).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}}


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


class LLM:
    def __init__(self, settings: Settings, client: OpenAI | None = None):
        self.settings = settings
        self.client = client or OpenAI()
        self.planner_prompt = (PROMPTS / "planner.md").read_text()
        self.vision_prompts = _load_vision_prompts()

    def _chat(self, model: str, messages: list[dict]) -> dict:
        resp = self.client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
        return _parse_json(resp.choices[0].message.content or "{}")

    def plan_next_action(self, task: str, history: str, observation: str,
                         screenshot: bytes | None = None) -> Plan:
        user_text = f"TASK: {task}\n\nHISTORY:\n{history or '(none yet)'}\n\nCURRENT PAGE:\n{observation}"
        content: list[dict] = [{"type": "text", "text": user_text}]
        model = self.settings.openai_model
        if screenshot:
            content.append(_image_part(screenshot))
            model = self.settings.vision_model
        messages = [{"role": "system", "content": self.planner_prompt},
                    {"role": "user", "content": content}]
        for attempt in range(2):
            data = self._chat(model, messages)
            try:
                return Plan.model_validate(data)
            except ValidationError as e:
                if attempt:
                    raise
                messages += [{"role": "assistant", "content": json.dumps(data)},
                             {"role": "user", "content": f"Invalid JSON for the schema: {e}. Reply again."}]
        raise RuntimeError("unreachable")

    def pick_mark(self, png: bytes, description: str, goal: str, ids: list[int]) -> int | None:
        prompt = self.vision_prompts["SOM"].format(description=description, goal=goal, ids=ids)
        data = self._chat(self.settings.vision_model, [
            {"role": "user", "content": [{"type": "text", "text": prompt}, _image_part(png)]},
        ])
        mark = data.get("id")
        return int(mark) if isinstance(mark, (int, float, str)) and str(mark).isdigit() and int(mark) in ids else None

    def locate_coordinates(self, png: bytes, description: str, goal: str,
                           width: int, height: int) -> tuple[int, int] | None:
        prompt = self.vision_prompts["COORDS"].format(
            description=description, goal=goal, width=width, height=height)
        data = self._chat(self.settings.vision_model, [
            {"role": "user", "content": [{"type": "text", "text": prompt}, _image_part(png)]},
        ])
        x, y = data.get("x"), data.get("y")
        if x is None or y is None:
            return None
        x, y = int(x), int(y)
        if not (0 <= x < width and 0 <= y < height):
            return None
        return x, y
