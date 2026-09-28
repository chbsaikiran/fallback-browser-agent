"""Each fixture forces a specific escalation tier; assert the outcome AND which tier worked."""
from agent.actions import Action
from agent.executor import Executor
from agent.observer import observe


class FakeLLM:
    """Stands in for the vision model: knows where things are on the fixtures."""

    def __init__(self, coords=None, mark=None):
        self.coords, self.mark = coords, mark
        self.calls = []

    def pick_mark(self, png, description, goal, ids):
        self.calls.append("som")
        return self.mark

    def locate_coordinates(self, png, description, goal, width, height):
        self.calls.append("coords")
        return self.coords


def by_name(obs, name):
    return next(e for e in obs.elements if e.name == name)


def status(page):
    return page.locator("#status").inner_text()


def test_tier1_plain_click(browser, open_fixture):
    page = open_fixture("normal.html")
    obs = observe(page)
    res = Executor(browser, None).execute(
        Action(type="click", element_id=by_name(obs, "Add to Cart").id, description="'Add to Cart' button"), obs)
    assert res.ok and res.tier == "t1:id"
    assert status(page) == "added to cart"


def test_tier1_select(browser, open_fixture):
    page = open_fixture("normal.html")
    obs = observe(page)
    sel = next(e for e in obs.elements if e.tag == "select")
    res = Executor(browser, None).execute(Action(type="select", element_id=sel.id, text="3"), obs)
    assert res.ok and res.tier == "t1:select_option"
    assert status(page) == "qty 3"


def test_tier2_covered_button_falls_back_to_js_click(browser, open_fixture):
    page = open_fixture("covered.html")
    obs = observe(page)
    res = Executor(browser, None).execute(
        Action(type="click", element_id=by_name(obs, "Book Tickets").id, description="'Book Tickets' button"), obs)
    assert res.ok and res.tier.startswith("t2:"), res.attempts
    assert status(page) == "booked"


def test_tier2_picky_input_needs_real_keystrokes(browser, open_fixture):
    page = open_fixture("autocomplete.html")
    obs = observe(page)
    city = next(e for e in obs.elements if e.tag == "input")
    res = Executor(browser, None).execute(
        Action(type="type", element_id=city.id, text="Bengaluru", description="'From city' input"), obs)
    assert res.ok and res.tier == "t2:per_key_typing", res.attempts
    assert page.locator("#city").input_value() == "Bengaluru"


def test_tier3_canvas_seat_uses_vision_coordinates(browser, open_fixture):
    page = open_fixture("canvas.html")
    obs = observe(page)
    llm = FakeLLM(coords=(20 + 2 * 70 + 25, 20 + 80 + 25))  # centre of seat B3
    res = Executor(browser, llm).execute(Action(type="click", description="seat 'B3' on the seat map"), obs)
    assert res.ok and res.tier == "t3:coords", res.attempts
    assert llm.calls == ["coords"]  # nothing to mark on a bare canvas, so SoM is skipped
    assert status(page) == "seat B3"


def test_iframe_elements_are_observed_and_clickable(browser, open_fixture):
    page = open_fixture("iframe.html")
    page.frame_locator("#pay").locator("button").wait_for()
    obs = observe(page)
    btn = by_name(obs, "Continue")
    assert btn.frame != page.main_frame and btn.y > 0
    res = Executor(browser, None).execute(
        Action(type="click", element_id=btn.id, description="'Continue' button"), obs)
    assert res.ok and res.tier == "t1:id"
    assert status(page) == "continued"


def test_sensitive_click_is_never_retried(browser, open_fixture):
    page = open_fixture("normal.html")
    page.evaluate("""() => {
        const b = document.getElementById('buy');
        b.onclick = () => { window.clicks = (window.clicks || 0) + 1; };
        b.onmousedown = e => e.preventDefault();  // keep focus unchanged
    }""")
    obs = observe(page)
    res = Executor(browser, None).execute(
        Action(type="click", element_id=by_name(obs, "Add to Cart").id, description="'Pay' button"),
        obs, single_shot=True)
    # The handler changes nothing visible, so a normal click would escalate through every tier.
    assert res.ok and res.tier == "t1:id"
    assert page.evaluate("window.clicks") == 1
