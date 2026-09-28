import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.5"))
    vision_model: str = field(default_factory=lambda: os.getenv("OPENAI_VISION_MODEL", "gpt-5.5"))
    max_steps: int = field(default_factory=lambda: int(os.getenv("MAX_STEPS", "40")))
    profile_dir: Path = field(default_factory=lambda: Path(os.getenv("PROFILE_DIR", "./profile")))
    start_url: str = field(default_factory=lambda: os.getenv("START_URL", "https://www.google.com"))
    headless: bool = field(default_factory=lambda: os.getenv("HEADLESS", "0") == "1")
    runs_dir: Path = Path("runs")
    viewport: tuple[int, int] = (1280, 860)
