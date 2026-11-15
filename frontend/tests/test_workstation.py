"""Browser regression checks against running EarthScope and verified POWER data.

Run with Python Playwright installed and Chromium available. Set
EARTHSCOPE_BROWSER_EXECUTABLE to use an existing Chromium-based browser.
"""

import csv
import os
import re

from playwright.sync_api import expect, sync_playwright


SITE_URL = os.environ.get("EARTHSCOPE_TEST_URL", "http://127.0.0.1:5173/").rstrip("/")
URL = f"{SITE_URL}/research/point"


def open_mode(page, name):
    picker = page.locator(".research-mode-picker")
    if picker.get_attribute("open") is None:
        picker.locator("summary").click()
    picker.get_by_role("link", name=name, exact=True).click()


def is_trend(response):
    return "/api/trend?" in response.url


def exercise_desktop(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, trend_requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda req: trend_requests.append(req.url) if "/api/trend?" in req.url else None)
    page.goto(URL, wait_until="domcontentloaded")
    expect(page.locator("html")).to_have_attribute("lang", "ru")
    run = page.get_by_role("button", name="Провести анализ")
    expect(run).to_be_enabled()
    expect(page.get_by_text("Анализ ещё не запущен", exact=True)).to_be_visible()

    map_element = page.locator(".leaflet-container:visible")
    marker = page.locator(".leaflet-marker-icon")
    expect(page.locator(".leaflet-overlay-pane path:not([d='M0 0'])").first).to_be_visible()
    map_element.evaluate("(node) => { window.testMapNode = node }")
    box = map_element.bounding_box()
    initial_marker = marker.bounding_box()
    assert box and initial_marker
    x, y = box["x"] + box["width"] * .4, box["y"] + box["height"] * .55
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 85, y + 35, steps=8)
    page.mouse.up()
    page.wait_for_function(
        "(x) => Math.abs(document.querySelector('.leaflet-marker-icon').getBoundingClientRect().x - x) > 35",
        arg=initial_marker["x"],
    )
    map_element.get_by_role("button", name="Приблизить").click()
    page.wait_for_function("() => !document.querySelector('.leaflet-zoom-anim')")
    before_click = map_element.locator(".leaflet-map-pane").evaluate("(node) => node.style.transform")
    latitude = page.get_by_label(re.compile("^Широта")).filter(visible=True)
    previous_latitude = latitude.input_value()
    map_element.click(position={"x": int(box["width"] * .27), "y": 160})
    expect(latitude).not_to_have_value(previous_latitude)
    assert map_element.locator(".leaflet-map-pane").evaluate("(node) => node.style.transform") == before_click
    changed_marker = marker.bounding_box()
    assert changed_marker and abs(changed_marker["x"] - (box["x"] + box["width"] / 2)) > 25

    variable = page.get_by_role("combobox")
    variable.select_option("precipitation")
    page.get_by_label("Первый год").filter(visible=True).fill("1991")
    page.get_by_label("Последний год").filter(visible=True).fill("1980")
    expect(run).to_be_disabled()
    page.get_by_label("Последний год").filter(visible=True).fill("2025")
    assert map_element.locator(".leaflet-map-pane").evaluate("(node) => node.style.transform") == before_click
    expect(page.get_by_text("Анализ ещё не запущен", exact=True)).to_be_visible()
    assert not trend_requests, trend_requests

    selected_latitude = latitude.input_value()
    longitude = page.get_by_label(re.compile("^Долгота")).filter(visible=True)
    selected_longitude = longitude.input_value()
    center_button = page.get_by_role("button", name="К выбранной точке")
    center_button.click()
    expect(latitude).to_have_value(selected_latitude)
    expect(longitude).to_have_value(selected_longitude)
    center_button.press("Enter")
    expect(latitude).to_have_value(selected_latitude)
    expect(longitude).to_have_value(selected_longitude)
    page.wait_for_function("""() => {
        const map = [...document.querySelectorAll('.leaflet-container')]
            .find(node => node.getClientRects().length > 0).getBoundingClientRect()
        const pin = document.querySelector('.leaflet-marker-icon').getBoundingClientRect()
        return Math.abs((pin.x + pin.width / 2) - (map.x + map.width / 2)) < 3
    }""")
    page.get_by_role("button", name="Бишкек, Кыргызстан").click()
    page.get_by_label("Первый год").filter(visible=True).fill("1990")
    assert not trend_requests, trend_requests
    submitted_view = map_element.locator(".leaflet-map-pane").evaluate("(node) => node.style.transform")
    with page.expect_response(is_trend, timeout=45000) as reply:
        run.click()
    assert reply.value.ok
    result = reply.value.json()
    assert result["variable"] == "precipitation" and result["source"]["dataset"] == "MERRA2"
    assert (result["latitude"], result["longitude"]) == (42.87, 74.6)
    chart = page.locator(".js-plotly-plot")
    expect(chart).to_be_visible(timeout=20000)
    traces = chart.evaluate("(node) => node.data.map(({name,y}) => ({name,y}))")
    assert len(traces) == 2
    assert traces[0]["y"] == [point["value"] for point in result["series"]]
    assert traces[1]["y"] == [point["fitted"] for point in result["series"]]
    expect(chart.get_by_text("Год (UTC)", exact=True)).to_be_visible()
    assert map_element.evaluate("(node) => node === window.testMapNode")
    assert map_element.locator(".leaflet-map-pane").evaluate("(node) => node.style.transform") == submitted_view

    with page.expect_download() as export:
        page.get_by_role("button", name="Скачать CSV").first.click()
    assert "Осадки" in export.value.suggested_filename
    with export.value.path().open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 36
    assert int(rows[0]["Год"]) == result["series"][0]["year"]
    assert float(rows[0]["Значение"]) == result["series"][0]["value"]
    assert rows[0]["Единица"] == "мм"

    variable.select_option("temperature")
    assert len(trend_requests) == 1, trend_requests
    with page.expect_response(is_trend, timeout=45000) as reply:
        run.click()
    assert reply.value.ok and reply.value.json()["variable"] == "temperature"
    expect(chart).to_have_attribute("aria-label", re.compile("Температура воздуха"))
    assert len(trend_requests) == 2, trend_requests
    assert map_element.evaluate("(node) => node === window.testMapNode")
    assert not errors, errors
    page.set_viewport_size({"width": 820, "height": 900})
    page.wait_for_function("document.documentElement.scrollWidth <= 820")
    page.close()


def exercise_mobile(browser):
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    requests = []
    page.on("request", lambda req: requests.append(req.url) if "/api/trend?" in req.url else None)
    page.goto(URL, wait_until="domcontentloaded")
    map_element = page.locator(".leaflet-container:visible")
    expect(map_element).to_be_visible()
    lat = page.get_by_label(re.compile("^Широта")).filter(visible=True)
    old = lat.input_value()
    map_element.tap(position={"x": 100, "y": 90})
    expect(lat).not_to_have_value(old)
    before_zoom = page.locator(".leaflet-overlay-pane path:not([d='M0 0'])").first.get_attribute("d")
    map_element.get_by_role("button", name="Приблизить").tap()
    expect(page.locator(".leaflet-overlay-pane path:not([d='M0 0'])").first).not_to_have_attribute("d", before_zoom)
    box = map_element.bounding_box()
    assert box
    pane = map_element.locator(".leaflet-map-pane")
    before_drag = pane.evaluate("(node) => node.style.transform")
    cdp = page.context.new_cdp_session(page)
    x, y = box["x"] + 150, box["y"] + 150
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for offset in (15, 30, 45, 60):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + offset, "y": y}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    assert pane.evaluate("(node) => node.style.transform") != before_drag
    assert not requests
    assert page.evaluate("document.documentElement.scrollWidth <= 390")
    page.get_by_role("button", name="Бишкек, Кыргызстан").tap()
    page.route("**/api/trend?*", lambda route: route.fulfill(status=502, json={
        "error": {"code": "POWER_UNAVAILABLE", "message": "Test-only upstream failure"}
    }))
    page.get_by_role("button", name="Провести анализ").tap()
    expect(page.get_by_text("NASA POWER не вернул данные.", exact=False)).to_be_visible()
    page.unroute("**/api/trend?*")
    page.get_by_role("button", name="Повторить запрос").tap()
    chart = page.locator(".js-plotly-plot")
    expect(chart).to_be_visible(timeout=25000)
    page.wait_for_function("""() => {
        const chart = document.querySelector('.js-plotly-plot')
        const svg = chart.querySelector('.main-svg')
        return svg && Math.abs(Number(svg.getAttribute('width')) - chart.clientWidth) < 2
    }""")
    assert page.evaluate("document.documentElement.scrollWidth <= 390")
    assert len(requests) == 2
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    held = []
    page.route("**/api/trend?*", lambda route: held.append(route))
    with page.expect_request(lambda req: "/api/trend?" in req.url):
        page.get_by_role("button", name="Провести анализ").tap()
    cancel = page.get_by_role("button", name="Отменить запрос")
    expect(cancel).to_be_visible()
    page.locator("form:visible").dispatch_event("submit")
    assert len(requests) == 3
    expect(chart).to_be_visible()
    cancel.tap()
    expect(page.get_by_text("Запрос отменён.", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Провести анализ")).to_be_enabled()
    for route in held:
        route.abort()
    assert not errors, errors
    page.close()


def exercise_comparison(browser, mobile=False):
    width = 390 if mobile else 1440
    page = browser.new_page(viewport={"width": width, "height": 900}, has_touch=mobile, is_mobile=mobile)
    requests, errors = [], []
    page.on("request", lambda request: requests.append(request) if "/api/compare" in request.url else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(URL, wait_until="domcontentloaded")
    open_mode(page, "Сравнение A / B")
    world = page.locator(".leaflet-container:visible")
    expect(world.locator(".leaflet-marker-icon")).to_have_count(2)
    world.evaluate("(node) => { window.testMapNode = node }")
    point_a = page.get_by_role("radio", name=re.compile("^A:"))
    point_b = page.get_by_role("radio", name=re.compile("^B:"))
    label = lambda point: point.locator("..").inner_text()
    a_initial = label(point_a)
    b_initial = label(point_b)
    point_b.check()
    pick = world.tap if mobile else world.click
    pick(position={"x": 100, "y": 120})
    assert label(point_b) != b_initial and label(point_a) == a_initial
    point_a.check()
    b_edited = label(point_b)
    pick(position={"x": 150, "y": 175})
    assert label(point_a) != a_initial and label(point_b) == b_edited
    page.get_by_role("button", name="Бишкек, Кыргызстан").click()
    point_b.check()
    page.get_by_role("button", name="Рейкьявик").click()
    world.get_by_role("button", name="Приблизить").click()
    page.wait_for_function("() => !document.querySelector('.leaflet-zoom-anim')")
    pane = world.locator(".leaflet-map-pane")
    transform = lambda: pane.evaluate("(node) => node.style.transform")
    before = transform()
    variable = page.get_by_role("combobox").first
    variable.select_option("precipitation")
    assert not requests and transform() == before
    run = page.get_by_role("button", name="Провести анализ")
    if mobile:
        page.route("**/api/compare", lambda route: route.fulfill(status=502, json={
            "error": {"code": "POWER_UNAVAILABLE", "message": "Test-only upstream failure"}
        }))
        run.click()
        expect(page.get_by_text("Сравнение не выполнено", exact=True)).to_be_visible()
        page.unroute("**/api/compare")
        run = page.get_by_role("button", name="Повторить запрос")
    with page.expect_response(lambda reply: reply.url.endswith("/api/compare"), timeout=45000) as reply:
        run.click()
    assert reply.value.ok
    result = reply.value.json()
    a, b = result["point_a"], result["point_b"]
    assert (a["latitude"], b["latitude"]) == (42.87, 64.147)
    assert a["unit"] == b["unit"] == "mm"
    assert result["comparison"]["years_used"] == 36 and result["comparison"]["p_value"] is None
    chart = page.locator(".js-plotly-plot")
    expect(chart).to_be_visible(timeout=20000)
    traces = lambda: chart.evaluate("(node) => node.data.map(trace => trace.y)")
    assert traces()[0] == [point["value"] for point in a["series"]]
    assert traces()[2] == [point["value"] for point in b["series"]]
    page.get_by_label("Значения", exact=True).select_option("anomaly")
    page.wait_for_function("expected => Math.abs(document.querySelector('.js-plotly-plot').data[0].y[0] - expected) < 1e-8",
        arg=a["series"][0]["value"] - sum(point["value"] for point in a["series"][:10]) / 10)
    with page.expect_download() as export:
        page.get_by_role("button", name="Скачать CSV · A и B").click()
    with export.value.path().open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 72 and {row["point"] for row in rows} == {"A", "B"}
    assert float(rows[0]["value"]) == a["series"][0]["value"]
    assert len(requests) == (2 if mobile else 1)
    if not mobile:
        page.get_by_label("Расположение", exact=True).select_option("side")
        expect(chart).to_have_count(2)
        assert chart.nth(0).evaluate("(node) => node.layout.yaxis.range") == chart.nth(1).evaluate("(node) => node.layout.yaxis.range")
        assert len(requests) == 1
        variable.select_option("temperature")
        with page.expect_response(lambda reply: reply.url.endswith("/api/compare"), timeout=45000) as reply:
            run.click()
        assert reply.value.ok and reply.value.json()["point_a"]["unit"] == "°C"
        expect(page.get_by_text("Температура воздуха · A / B · 1990–2025 UTC", exact=True)).to_be_visible()
        assert len(requests) == 2
    assert world.evaluate("(node) => node === window.testMapNode") and transform() == before
    assert page.evaluate("width => document.documentElement.scrollWidth <= width", width)
    assert not errors, errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("EARTHSCOPE_BROWSER_EXECUTABLE"))
        exercise_desktop(browser)
        exercise_mobile(browser)
        exercise_comparison(browser)
        exercise_comparison(browser, mobile=True)
    print("PASS: desktop/mobile single and comparison maps, manual requests, anomalies, CSV and error recovery")
