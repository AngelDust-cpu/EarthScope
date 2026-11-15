"""Responsive browser smoke for the EarthScope 3.0 workstation with the real local API."""
import os

from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("EARTHSCOPE_TEST_URL", "http://127.0.0.1:5173/")
EDGE = os.environ.get("EARTHSCOPE_BROWSER_EXECUTABLE")


def test_responsive_workstation_and_chart_matches_api():
    other_modes = {
        "Климатический профиль": ".analysis-controls-slot .research-profile-form",
        "Сравнение A / B": ".station-query form",
        "Климатические аномалии": ".analysis-controls-slot .calendar-query",
        "Сезонный анализ": ".analysis-controls-slot .calendar-query",
        "Погодные экстремумы": ".analysis-controls-slot form",
        "Карта климатических трендов": ".analysis-controls-slot .rtm-controls",
        "Детектив": None,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=EDGE)
        for width in (1440, 820, 390):
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(URL)
            navigation = page.get_by_role("navigation")
            for mode, control in other_modes.items():
                tab = navigation.get_by_role("button", name=mode, exact=True)
                tab.click()
                expect(tab).to_have_attribute("aria-pressed", "true")
                if control:
                    expect(page.locator(".research-rail").locator(control)).to_be_visible()
                else:
                    expect(page.locator(".analysis-controls-slot")).to_be_empty()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, mode)
            navigation.get_by_role("button", name="Одна точка", exact=True).click()
            with page.expect_response(lambda response: "/api/trend?" in response.url, timeout=30_000) as result:
                page.get_by_role("button", name="Провести анализ", exact=True).click()
            assert result.value.ok
            chart = page.locator(".js-plotly-plot:visible")
            expect(chart.locator(".scatterlayer")).to_be_visible(timeout=15_000)
            assert chart.evaluate("node => node.data[0].y") == [point["value"] for point in result.value.json()["series"]]
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            print(f"PASS EarthScope navigation, viewport and NASA series at {width}px")
            page.close()
        browser.close()


if __name__ == "__main__":
    test_responsive_workstation_and_chart_matches_api()
