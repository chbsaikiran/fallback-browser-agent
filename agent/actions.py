from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ActionType = Literal[
    "click", "type", "select", "press_key", "scroll", "navigate",
    "wait", "go_back", "switch_tab", "close_tab", "ask_user", "done",
]


def _drop_nulls(data):
    """LLMs often send null for fields that don't apply (e.g. "direction": null on a click).
    Dropping them lets the field defaults apply instead of failing validation."""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if v is not None}
    return data


class Action(BaseModel):
    type: ActionType
    element_id: Optional[int] = None
    text: Optional[str] = None          # text to type, option, key, url, tab index, question, final summary
    description: str = ""               # natural-language description of the target, used by fallbacks
    submit: bool = False                # for "type": press Enter afterwards
    direction: Literal["up", "down"] = "down"

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data):
        data = _drop_nulls(data)
        if isinstance(data, dict):
            d = data.get("direction")
            if isinstance(d, str) and d.lower() not in ("up", "down"):
                data = {**data, "direction": "down"}  # "none", "", "left" ... -> harmless default
            elif isinstance(d, str):
                data = {**data, "direction": d.lower()}
        return data


class Plan(BaseModel):
    thought: str = ""
    action: Action
    is_sensitive: bool = False          # payment / final confirmation / irreversible
    remember: str = ""                  # facts learned on this page (price, title, which items are done)

    @model_validator(mode="before")
    @classmethod
    def _nulls(cls, data):
        return _drop_nulls(data)


class ActionResult(BaseModel):
    ok: bool
    tier: Optional[str] = None          # which strategy finally worked, e.g. "t1:role", "t2:js_click", "t3:som"
    message: str = ""
    attempts: list[str] = Field(default_factory=list)
