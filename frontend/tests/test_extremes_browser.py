"""Local Edge checks with real NASA POWER values in the configured cache."""
import os
import re
import csv

from playwright.sync_api import expect, sync_playwright
from test_workstation import URL


def response_is_extremes(response):
    return "/api/extremes?" in response.url


def run(page, button):
    with page.expect_response(response_is_extremes, timeout=45000) as output:
        button.click()
    assert output.value.ok, output.value.text()
    return output.value.json()


def detective(browser):
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    page.goto(URL, wait_until="domcontentloaded")
    page.get_by_role("button", name="Детектив", exact=True).click()
    wizard = page.locator("#detective-workspace")
    parameters = page.get_by_role("region", name="Параметры исследования детектива")
    question = wizard.get_by_role("button", name="Стало ли больше экстремально жарких дней?", exact=True)
    question.tap()
    parameters.get_by_role("button", name="Далее", exact=True).tap()
    threshold = parameters.get_by_label("Порог жары, °C", exact=True)
    threshold.fill("101")
    submit = parameters.get_by_role("button", name="Провести анализ", exact=True)
    expect(submit).to_be_disabled()
    threshold.fill("30,5")
    wizard.get_by_role("button", name="Назад", exact=True).tap()
    parameters.get_by_role("button", name="Далее", exact=True).tap()
    expect(threshold).to_have_value("30,5")
    data = run(page, submit)
    assert data["threshold"] == 30.5 and data["event"] == "hot_days"
    assert data["source"]["dataset"] == "MERRA2"
    selected_year = data["series"][10]
    assert wizard.locator('svg[role="radiogroup"] [data-year-label]').count() == 1
    bar = wizard.locator('svg[role="radiogroup"] [role="radio"]').nth(10)
    hitbox_width = bar.locator("rect").first.evaluate("node => node.getBBox().width")
    assert hitbox_width >= 24
    bar.tap()
    expect(wizard.locator('[id$="-selection"]')).to_contain_text(str(selected_year["year"]))
    expect(wizard.locator('[id$="-selection"]')).to_contain_text(
        str(selected_year["events"]) if selected_year["events"] is not None else "Неполный год"
    )
    expect(wizard.get_by_role("heading", name="Что удалось выяснить")).to_be_visible()
    expect(wizard.get_by_role("button", name=re.compile("Скачать CSV")).first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= 390")
    wizard.get_by_role("button", name="Новое исследование").tap()
    expect(question).to_be_visible()
    question.tap()
    parameters.get_by_role("button", name="Далее", exact=True).tap()
    expect(threshold).to_have_value("30")
    expect(parameters.get_by_role("button", name="Открыть сохранённый ответ")).to_have_count(0)
    page.close()


def workstation(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    requests, errors = [], []
    page.on("request", lambda request: requests.append(request) if "/api/extremes?" in request.url else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(URL, wait_until="domcontentloaded")
    page.get_by_role("button", name="Погодные экстремумы", exact=True).click()
    scope = page.get_by_role("complementary", name="Параметры экстремальных событий")
    result = page.locator("#extremes-workspace")
    world = page.locator(".leaflet-container:visible")
    world.evaluate("(node) => { window.extremesMap = node }")
    submit = scope.get_by_role("button", name="Рассчитать экстремумы", exact=True)
    for name, code, param in [
        ("Жаркие дни", "hot_days", "T2M_MAX"),
        ("Холодные ночи", "cold_nights", "T2M_MIN"),
        ("Сильные осадки", "heavy_rain", "PRECTOTCORR"),
    ]:
        old_count = len(requests)
        scope.get_by_role("button", name=re.compile(name)).click()
        assert len(requests) == old_count
        data = run(page, submit)
        assert data["event"] == code and data["source"]["parameter"] == param
        assert len(data["series"]) == 36 and data["source"]["cached"]
        assert next(p for p in data["series"] if p["year"] == 2020)["valid_days"] == 366
        expect(result.get_by_role("button", name="Скачать CSV")).to_be_visible()
        assert world.evaluate("(node) => node === window.extremesMap")
    # A maximum precipitation threshold produces true zero counts from the same NASA rows.
    scope.get_by_label(re.compile("Порог")).fill("2000")
    zero_data = run(page, submit)
    assert all(year["events"] == 0 for year in zero_data["series"] if year["events"] is not None)
    year_index = next(i for i, year in enumerate(zero_data["series"]) if year["events"] == 0)
    zero_bar = result.locator('svg[role="radiogroup"] [role="radio"]').nth(year_index)
    first_bar = result.locator('svg[role="radiogroup"] [role="radio"]').nth(0)
    next_index = 1 if year_index == 0 else 0
    next_bar = result.locator('svg[role="radiogroup"] [role="radio"]').nth(next_index)
    original_year = zero_data["series"][0]["year"]
    next_year = zero_data["series"][next_index]["year"]
    first_bar.focus()
    first_bar.press("ArrowRight")
    expect(next_bar).to_be_focused()
    expect(first_bar).to_have_attribute("aria-checked", "true")
    expect(result.locator('[id$="-selection"]')).to_contain_text(str(original_year))
    next_bar.press("Enter")
    expect(next_bar).to_have_attribute("aria-checked", "true")
    expect(result.locator('[id$="-selection"]')).to_contain_text(str(next_year))
    zero_bar.focus()
    zero_bar.press("Space")
    expect(result.locator('[id$="-selection"]')).to_contain_text("0 дней")
    expect(result.locator('[id$="-selection"]')).to_contain_text(str(zero_data["series"][year_index]["year"]))
    assert result.get_by_text("Среднее за полный год", exact=True).is_visible()
    assert result.get_by_text("Изменение числа дней за десятилетие", exact=True).is_visible()
    svg = result.locator('svg[role="radiogroup"]').first
    assert svg.evaluate("node => node.getBoundingClientRect().width") >= 80 + len(zero_data["series"]) * 28
    click_index = min(year_index + 1, len(zero_data["series"]) - 1)
    click_year = zero_data["series"][click_index]
    result.locator('svg[role="radiogroup"] [role="radio"]').nth(click_index).click()
    expect(result.locator('[id$="-selection"]')).to_contain_text(str(click_year["year"]))
    expect(result.locator('[id$="-selection"]')).to_contain_text("0 дней")
    # The three event choices remain in one aligned grid despite different label lengths.
    cards = [scope.get_by_role("button", name=re.compile(name)).bounding_box() for name in ["Жаркие дни", "Холодные ночи", "Сильные осадки"]]
    assert all(card for card in cards)
    assert max(card["height"] for card in cards) - min(card["height"] for card in cards) <= 2
    with page.expect_download() as download:
        result.get_by_role("button", name="Скачать CSV").click()
    with download.value.path().open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 36 and int(rows[0]["events"]) == data["series"][0]["events"]
    scope.get_by_role("checkbox", name=re.compile("Добавить точку B")).check()
    before = len(requests)
    run(page, submit)
    expect(result.get_by_role("region", name="Результат точки B")).to_be_visible()
    expect(result.get_by_text("Сравнение только описательное.", exact=False)).to_be_visible()
    assert len(requests) == before + 2
    for width, height in [(768, 1024), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        small_cards = [scope.get_by_role("button", name=re.compile(name)).bounding_box() for name in ["Жаркие дни", "Холодные ночи", "Сильные осадки"]]
        assert max(card["height"] for card in small_cards) - min(card["height"] for card in small_cards) <= 2
    assert not errors, errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("EARTHSCOPE_BROWSER_EXECUTABLE"))
        detective(browser)
        workstation(browser)
    print("PASS: all three NASA events, A/B, CSV, stable map and mobile Detective with threshold/reset")
