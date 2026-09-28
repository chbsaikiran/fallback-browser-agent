"""CLI entry point.

  uv run main.py "search amazon.in for a usb-c cable under 500 and add the best rated to cart"
  uv run main.py --observe https://www.amazon.in      # debug: print what the agent "sees"
"""
import argparse
import os
import sys

from rich.console import Console

from agent.config import Settings

console = Console()


def observe_only(url: str) -> None:
    from agent.browser import Browser
    from agent.observer import observe

    b = Browser(Settings())
    page = b.start()
    page.goto(url, wait_until="domcontentloaded")
    b.settle()
    b.dismiss_popups()
    console.print(observe(page).to_prompt())
    input("Press Enter to close...")
    b.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM browser agent for shopping and bookings")
    ap.add_argument("task", nargs="?", help="what to do, in plain language")
    ap.add_argument("--observe", metavar="URL", help="print the element list for a URL and exit")
    ap.add_argument("--max-steps", type=int)
    args = ap.parse_args()

    if args.observe:
        return observe_only(args.observe)
    if not args.task:
        ap.error("task is required")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in.")

    from agent.agent import BrowserAgent

    settings = Settings()
    if args.max_steps:
        settings.max_steps = args.max_steps
    BrowserAgent(args.task, settings).run()


if __name__ == "__main__":
    main()
