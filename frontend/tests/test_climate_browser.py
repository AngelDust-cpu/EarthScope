"""Edge smoke test for 2.3 against local API and real NASA POWER data."""
import os
import re
import csv

from playwright.sync_api import expect, sync_playwright
from test_workstation import URL


def submit(page, scope, route):
    with page.expect_response(lambda response: f"/api/{route}?" in response.url, timeout=60000) as result:
        scope.get_by_role("button", name=re.compile("Провести анализ|Рассчитать|Выполнить анализ")).first.click()
    assert result.value.ok, result.value.text()
    return result.value.json()


def detective(browser):
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    page.goto(URL, wait_until="domcontentloaded")
    page.get_by_role("button", name="Детектив", exact=True).click()
    wizard = page.locator("#detective-workspace")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for title, route in [("Какие годы были необычно тёплыми или холодными?", "anomalies"),
                         ("Какой сезон меняется сильнее всего?", "seasonal")]:
        wizard.get_by_role("button", name=title, exact=True).tap()
        wizard.get_by_role("button", name="Далее", exact=True).tap()
        result = submit(page, wizard, route)
        assert result["source"]["dataset"] == "MERRA2"
        expect(wizard.get_by_role("img").first).to_be_visible()
        if route == "anomalies":
            assert result["baseline_complete_years"] == 30
            assert result["baseline_mean"] is not None
            assert {point["anomaly"] > 0 for point in result["series"] if point["anomaly"] is not None} == {True, False}
            expect(wizard.locator("svg[role=img] line[stroke-dasharray]").first).to_be_attached()
        else:
            assert [item["period"] for item in result["periods"]] == ["winter", "spring", "summer", "autumn"]
        assert page.evaluate("document.documentElement.scrollWidth <= 390")
        wizard.get_by_role("button", name="Новое исследование").tap()
        expect(wizard.get_by_role("button", name=title, exact=True)).to_be_visible()
    assert not errors, errors
    page.close()


def workstation(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    analysis_requests = []
    page.on("request", lambda request: analysis_requests.append(request.url)
            if re.search(r"/api/(anomalies|seasonal)\?", request.url) else None)
    page.goto(URL, wait_until="domcontentloaded")
    page.get_by_role("button", name="Климатические аномалии", exact=True).click()
    form = page.get_by_role("complementary", name="Параметры климатического анализа")
    world = page.locator(".leaflet-container:visible")
    world.evaluate("(node) => { window.climateMap = node }")
    form.get_by_label("Первый год", exact=True).fill("2021")
    form.get_by_label("Последний год", exact=True).fill("2025")
    assert not analysis_requests
    result = submit(page, form, "anomalies")
    assert result["baseline_end_year"] == 2020 < result["start_year"]
    assert result["unit"] == "°C" and result["baseline_complete_years"] == 30
    pane = page.locator("#climate-workspace")
    with page.expect_download() as export:
        pane.get_by_role("button", name="Скачать CSV").click()
    with export.value.path().open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 5 and float(rows[0]["anomaly"]) == result["series"][0]["anomaly"]
    assert world.evaluate("(node) => node === window.climateMap")
    page.get_by_role("button", name="Сезонный анализ", exact=True).click()
    form.get_by_label("Первый год", exact=True).fill("1990")
    months = form.get_by_role("button", name="Месяцы", exact=True)
    if months.count():
        months.click()
        form.get_by_role("button", name="Февраль", exact=True).click()
    else:
        form.get_by_label(re.compile("Сезон или месяц")).select_option("m02")
    form.get_by_label(re.compile("Показатель")).select_option("precipitation")
    assert len(analysis_requests) == 1
    result = submit(page, form, "seasonal")
    assert result["period"] == "m02" and result["unit"] == "mm" and result["aggregation"] == "sum"
    feb = next(row for row in result["periods"][0]["series"] if row["year"] == 2020)
    assert feb["valid_days"] == feb["expected_days"] == 29 and feb["value"] is not None
    assert len(analysis_requests) == 2
    assert world.evaluate("(node) => node === window.climateMap")
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("EARTHSCOPE_BROWSER_EXECUTABLE"))
        detective(browser)
        workstation(browser)
    print("PASS: standalone + Detective, external baseline, CSV, February leap day, map stability, zero line and mobile reset")
