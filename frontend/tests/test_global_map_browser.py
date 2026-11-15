"""Browser regression for the current global map. Requires local prepared POWER archive."""
import os
from collections import Counter

from playwright.sync_api import expect, sync_playwright

from test_workstation import URL, open_mode
EDGE = os.environ.get("EARTHSCOPE_BROWSER_EXECUTABLE",
                      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


def test_global_map():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE)
        for width in (1440, 390):
            page = browser.new_page(viewport={"width": width, "height": 900}, accept_downloads=True)
            requests = Counter()
            page.on("request", lambda request: requests.update([request.url.split("?")[0].split("/api/")[-1]]))
            page.goto(URL, wait_until="domcontentloaded")
            open_mode(page, "Карта климатических трендов")
            map_element = page.locator(".rtm-map-canvas")
            canvas = map_element.locator("canvas.rtm-raster")
            expect(canvas).to_be_visible(timeout=25000)
            assert requests["maps/world"] == 1

            form = page.get_by_role("form", name="Выбор ячейки по координатам")
            form.get_by_label("Широта").fill("42,87")
            form.get_by_label("Долгота").fill("74,6")
            with page.expect_response(lambda r: "/api/maps/world/cell?" in r.url) as cell:
                form.get_by_label("Долгота").press("Enter")
            assert cell.value.ok and cell.value.json()["parameter"] == "T2M"
            pdf_button = page.get_by_role("button", name="Сформировать PDF-отчёт · ячейка мира")
            expect(pdf_button).to_be_visible()
            with page.expect_download() as pending:
                pdf_button.click()
            assert pending.value.suggested_filename.endswith(".pdf")

            page.get_by_role("button", name="Значимость BY", exact=True).click()
            page.get_by_role("button", name="Исходная сетка", exact=True).click()
            map_element.scroll_into_view_if_needed()
            region = map_element.bounding_box()
            x, y = region["x"] + region["width"] * .52, region["y"] + region["height"] * .45
            page.mouse.move(x, y)
            expect(map_element.locator(".rtm-cell-tooltip")).to_contain_text("q")
            page.get_by_role("button", name="Плавная карта", exact=True).click()
            map_element.get_by_role("button", name="Увеличить").click()
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 45, y + 20, steps=5)
            page.mouse.up()
            assert requests["maps/world"] == 1
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            print(f"PASS global map at {width}px: keyboard, PDF, BY tooltip, pan/zoom without layer reload", flush=True)
            page.close()
        browser.close()


def test_point_map_normalizes_longitude_beyond_dateline():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(URL)
        map_element = page.locator(".leaflet-container:visible")
        map_element.scroll_into_view_if_needed()
        box = map_element.bounding_box()
        y = box["y"] + box["height"] * 0.5
        for _ in range(3):
            page.mouse.move(box["x"] + box["width"] * 0.9, y)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.1, y, steps=8)
            page.mouse.up()
        map_element.click(position={"x": box["width"] / 2, "y": box["height"] / 2})
        longitude = page.get_by_role("textbox", name="Долгота")
        assert -180 <= float(longitude.input_value().replace(",", ".")) < 0
        expect(page.get_by_role("button", name="Провести анализ", exact=True)).to_be_enabled()
        print("PASS point map longitude wraps across the antimeridian", flush=True)
        browser.close()


if __name__ == "__main__":
    test_global_map()
    test_point_map_normalizes_longitude_beyond_dateline()
