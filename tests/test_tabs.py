"""Products opened in a new tab: "back" must return to the results tab, not press browser Back."""
import time

from agent.actions import Action
from agent.executor import Executor
from agent.observer import observe


def click(ex, page, name):
    obs = observe(page)
    el = next(e for e in obs.elements if e.name == name)
    return ex.execute(Action(type="click", element_id=el.id, description=f"'{name}' link"), obs)


def wait_for_tabs(browser, n):
    for _ in range(50):
        if len(browser.tabs()) == n:
            return
        time.sleep(0.1)


def test_new_tab_is_followed_and_go_back_returns_to_results(browser, open_fixture):
    results = open_fixture("results.html")
    ex = Executor(browser, None)

    assert click(ex, results, "Cable A").ok
    wait_for_tabs(browser, 2)
    assert browser.current().url.endswith("product.html")
    assert "<-- CURRENT" in browser.tabs_prompt().splitlines()[2]

    res = ex.execute(Action(type="go_back"), observe(browser.current()))
    assert res.ok and "previous tab" in res.message
    assert browser.current() is results and len(browser.tabs()) == 1

    # And the next product can be opened from the same results tab
    assert click(ex, results, "Cable B").ok
    wait_for_tabs(browser, 2)
    assert browser.current().url.endswith("product.html?b")


def test_switch_and_close_tab(browser, open_fixture):
    results = open_fixture("results.html")
    ex = Executor(browser, None)
    click(ex, results, "Cable A")
    wait_for_tabs(browser, 2)

    assert ex.execute(Action(type="switch_tab", text="0"), observe(browser.current())).ok
    assert browser.current() is results
    assert ex.execute(Action(type="switch_tab", text="1"), observe(browser.current())).ok
    assert ex.execute(Action(type="close_tab"), observe(browser.current())).ok
    assert browser.current() is results and len(browser.tabs()) == 1


def test_go_back_in_same_tab_still_uses_history(browser, open_fixture):
    page = open_fixture("results.html")
    page.goto(page.url.replace("results.html", "product.html"))
    res = Executor(browser, None).execute(Action(type="go_back"), observe(page))
    assert res.ok and res.message == "went back in history"
    assert page.url.endswith("results.html")
