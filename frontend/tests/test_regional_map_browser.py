"""Browser regression for the NASA POWER-backed regional trend map.

Requires the documented local frontend/backend servers and Edge via
EARTHSCOPE_BROWSER_EXECUTABLE. The regional data query is real; the final
point-analysis request is intercepted after its URL is inspected.
"""
import os
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


URL = os.environ.get("EARTHSCOPE_TEST_URL", "http://127.0.0.1:5173/")
EDGE = os.environ.get(
    "EARTHSCOPE_BROWSER_EXECUTABLE",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def build_map(page):
    page.get_by_role("button", name="Карта климатических трендов", exact=True).click()
    build = page.get_by_role("button", name="Построить карту", exact=True)
    with page.expect_response(
        lambda response: "/api/maps/trends?" in response.url,
        timeout=45_000,
    ) as result:
        build.click()
    assert result.value.ok, result.value.text()
    return result.value.json()


def exercise(page, mobile=False):
    page_errors, regional_requests = [], []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "request",
        lambda request: regional_requests.append(request.url)
        if "/api/maps/trends?" in request.url
        else None,
    )
    page.goto(URL, wait_until="domcontentloaded")
    data = build_map(page)
    assert len(data["features"]) == 209
    assert data["metadata"]["source"]["dataset"] == "MERRA2"
    grid = page.locator(".rtm-map .leaflet-container")
    grid.wait_for(state="visible")
    grid.evaluate("node => { window.regionalMapNode = node }")

    legend = page.get_by_role("group", name="Легенда карты")
    expect(legend).to_contain_text("°C/десятилетие")
    expect(legend).to_contain_text("Недостаточно данных")
    expect(legend).to_contain_text("Цветовая поверхность сглажена")
    if mobile:
        legend.scroll_into_view_if_needed()
    legend_box = legend.bounding_box()
    assert legend_box and legend_box["y"] + legend_box["height"] <= page.viewport_size["height"] + 1
    grid.scroll_into_view_if_needed()
    canvas = grid.locator(".leaflet-regional-cells-pane canvas")
    expect(canvas).to_be_visible()
    # Measure the actual filled pixels, rather than the padded canvas element.
    assert canvas.evaluate('''node => {
        const { width, height } = node;
        const pixels = node.getContext('2d').getImageData(0, 0, width, height).data;
        let left = width, right = 0, top = height, bottom = 0;
        for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
            if (pixels[(y * width + x) * 4 + 3] < 250) continue;
            left = Math.min(left, x); right = Math.max(right, x);
            top = Math.min(top, y); bottom = Math.max(bottom, y);
        }
        const rect = node.getBoundingClientRect();
        const map = node.closest('.leaflet-container').getBoundingClientRect();
        return Math.max((right-left)*rect.width/width/map.width,
                        (bottom-top)*rect.height/height/map.height) >= 0.8;
    }''')
    box = grid.bounding_box()
    assert box
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    if mobile:
        page.touchscreen.tap(x, y)
    else:
        page.mouse.move(x, y)
        expect(grid.locator(".rtm-cell-tooltip")).to_contain_text("°C/десятилетие")
        page.mouse.click(x, y)
    details = page.locator(".rtm-details")
    expect(details).to_be_visible()
    expect(grid.locator(".rtm-selected-outline")).to_be_visible()
    assert grid.locator(".rtm-selected-outline").count() == 1
    selected = page.get_by_label("Выбрать ячейку (клавиатура)").input_value()
    properties = next(feature["properties"] for feature in data["features"] if feature["id"] == selected)
    expect(details.locator("h3")).to_have_text(
        f"{properties['latitude']:.3f}° с. ш., {properties['longitude']:.3f}° в. д."
    )
    slope = properties["trend"]["per_decade"]
    number = page.evaluate("number => Math.abs(number).toLocaleString('ru-RU', { maximumFractionDigits: 3 })", slope)
    sign = "+" if slope > 0 else "−" if slope < 0 else ""
    expect(details.locator(".rtm-primary-value")).to_have_text(f"{sign}{number} °C/десятилетие")
    expect(details).to_contain_text("NASA POWER")
    expect(details).to_contain_text("Daily UTC")
    expect(page.get_by_role("button", name="Исследовать эту точку")).to_be_visible()

    if mobile:
        expect(page.get_by_role("link", name="К данным ячейки")).to_be_visible()
    page.mouse.move(1, 1)  # close hover tooltip before taking the screenshot
    page.screenshot(
        path=".tools/earthscope-raster-mobile.png" if mobile
        else ".tools/earthscope-raster-after.png",
        full_page=True,
    )

    smooth_image = canvas.evaluate("node => node.toDataURL()")
    native = page.get_by_role("button", name="Исходная сетка", exact=True)
    native.click()
    expect(native).to_have_attribute("aria-pressed", "true")
    assert canvas.evaluate("node => node.toDataURL()") != smooth_image
    expect(details.locator(".rtm-primary-value")).to_have_text(f"{sign}{number} °C/десятилетие")
    page.get_by_role("button", name="Плавная карта", exact=True).click()
    assert canvas.evaluate("node => node.toDataURL()") == smooth_image

    # Switching map display must not fetch.
    significance = page.get_by_role("button", name="Значимость BY", exact=True)
    significance.click()
    expect(significance).to_have_attribute("aria-pressed", "true")
    expect(legend).to_contain_text("q ≤ 0,10")
    assert "°C/десятилетие" not in legend.inner_text()
    expect(native).to_be_disabled()
    assert canvas.evaluate('''node => {
        const colors = new Set();
        const pixels = node.getContext('2d').getImageData(0, 0, node.width, node.height).data;
        for (let i = 0; i < pixels.length; i += 4)
            if (pixels[i+3]) colors.add((pixels[i] << 16) | (pixels[i+1] << 8) | pixels[i+2]);
        return colors.size === 2;
    }''')
    assert len(regional_requests) == 1
    assert grid.evaluate("node => node === window.regionalMapNode")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if not mobile:
        page.screenshot(path=".tools/earthscope-raster-significance.png", full_page=True)

    # Interacting with the viewport must not fetch or replace the map.
    box = grid.bounding_box()
    assert box
    pane = grid.locator(".leaflet-map-pane")
    before_pan = pane.evaluate("node => node.style.transform")
    page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.55)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.55 + 40, box["y"] + box["height"] * 0.55 + 20, steps=5)
    page.mouse.up()
    assert pane.evaluate("node => node.style.transform") != before_pan
    before_zoom = canvas.evaluate("node => node.toDataURL()")
    before_width = canvas.evaluate("node => node.style.width")
    grid.get_by_role("button", name="Увеличить").click()
    assert canvas.evaluate("node => node.style.width") != before_width
    assert canvas.evaluate("node => node.toDataURL()") == before_zoom
    zoomed_view = pane.evaluate("node => node.style.transform")
    trend = page.get_by_role("button", name="Тренд", exact=True)
    trend.click()
    expect(trend).to_have_attribute("aria-pressed", "true")
    assert pane.evaluate("node => node.style.transform") == zoomed_view
    grid.get_by_role("button", name="К региону").click()
    assert canvas.evaluate("node => node.style.width") == before_width
    assert canvas.evaluate("node => node.toDataURL()") == smooth_image
    assert len(regional_requests) == 1
    assert grid.evaluate("node => node === window.regionalMapNode")

    # Explicit action forwards selected center, variable, and exact dates to
    # the legacy single-point API; abort the point request after inspecting it.
    point_requests = []
    def intercept(route):
        point_requests.append(route.request.url)
        route.abort()
    page.route("**/api/trend?*", intercept)
    page.get_by_role("button", name="Исследовать эту точку").click()
    page.wait_for_timeout(150)
    assert point_requests
    query = parse_qs(urlparse(point_requests[0]).query)
    assert query["variable"] == ["temperature"]
    assert query["start_year"] == ["1990"] and query["end_year"] == ["2025"]
    assert query["latitude"] and query["longitude"]
    assert not page_errors, page_errors
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_regional_map_desktop_and_mobile():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=EDGE, headless=True)
        desktop = browser.new_page(viewport={"width": 1440, "height": 1000})
        exercise(desktop)
        mobile = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        exercise(mobile, mobile=True)
        browser.close()


def test_regional_map_keeps_layer_after_error_and_retry():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=EDGE, headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        data = build_map(page)
        grid = page.locator(".rtm-map .leaflet-container")
        grid.evaluate("node => { window.regionalMapNode = node }")
        chooser = page.get_by_label("Выбрать ячейку (клавиатура)")
        chooser.select_option(index=2)
        details = page.locator(".rtm-details")
        expect(details).to_be_visible()

        build = page.get_by_role("button", name="Построить карту", exact=True)
        endpoint = "**/api/maps/trends?*"
        page.route(endpoint, lambda route: route.fulfill(status=503, json={
            "error": {"code": "POWER_UNAVAILABLE", "message": "NASA POWER returned HTTP 503"},
        }))
        build.click()
        expect(page.locator(".rtm [role=alert]")).to_contain_text("NASA POWER")
        expect(details).to_be_visible()
        page.unroute(endpoint)

        # Simulate an incomplete response from an intermediary, not NASA data.
        malformed = {**data, "features": [{**data["features"][0], "geometry": {
            "type": "Polygon", "coordinates": [],
        }}, *data["features"][1:]]}
        page.route(endpoint, lambda route: route.fulfill(json=malformed))
        build.click()
        expect(page.locator(".rtm [role=alert]")).to_contain_text("Сервер вернул некорректный слой")
        expect(details).to_be_visible()
        page.unroute(endpoint)

        page.get_by_label("Показатель", exact=True).select_option("precipitation")
        with page.expect_response(lambda response: "/api/maps/trends?" in response.url) as reply:
            build.click()
        assert reply.value.ok, reply.value.text()
        assert reply.value.json()["metadata"]["parameter"] == "PRECTOTCORR"
        expect(page.locator(".rtm [role=alert]")).to_have_count(0)
        expect(details).to_contain_text("Годовая сумма осадков (мм)")
        expect(page.get_by_role("group", name="Легенда карты")).to_contain_text("мм/десятилетие")
        assert grid.evaluate("node => node === window.regionalMapNode")
        canvas = grid.locator(".rtm-raster")
        before = canvas.evaluate("node => node.toDataURL()")
        with page.expect_response(lambda response: response.url.endswith("kyrgyzstan-wb-adm0.geojson")):
            page.get_by_label("Обрезать по границе Кыргызстана").check()
        page.wait_for_function("before => document.querySelector('.rtm-raster').toDataURL() !== before", arg=before)
        assert canvas.evaluate("node => node.getContext('2d').getImageData(0, 0, 1, 1).data[3]") == 0
        assert canvas.evaluate("node => node.getContext('2d').getImageData(node.width / 2, node.height / 2, 1, 1).data[3]") == 255
        page.get_by_role("button", name="К региону").click()
        page.screenshot(path=".tools/earthscope-raster-kyrgyzstan.png", full_page=True)
        page.get_by_label("Обрезать по границе Кыргызстана").uncheck()
        assert canvas.evaluate("node => node.toDataURL()") == before
        browser.close()


def test_raster_keeps_missing_cells_transparent():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=EDGE, headless=True)
        page = browser.new_page()
        page.goto(URL)
        data = build_map(page)
        # Fault injection into a copy of the API response. This is not an observation.
        center = next(feature for feature in data["features"] if
                      feature["properties"]["latitude"] == 41.5 and feature["properties"]["longitude"] == 75)
        center["properties"]["trend"] = None
        page.route("**/api/maps/trends?*", lambda route: route.fulfill(json=data))
        with page.expect_response(lambda response: "/api/maps/trends?" in response.url):
            page.get_by_role("button", name="Построить карту", exact=True).click()
        canvas = page.locator(".rtm-raster")
        expect(canvas).to_be_visible()
        assert canvas.evaluate('''node => {
            const context = node.getContext('2d');
            const x = Math.floor(node.width / 2), y = Math.floor(node.height / 2);
            return context.getImageData(x, y, 1, 1).data[3] === 0 &&
                   context.getImageData(x+Math.floor(node.width/14), y, 1, 1).data[3] === 255;
        }''')
        browser.close()
