"""Local Edge checks with cached NASA data and controlled failure/race cases."""
import os
import re

from playwright.sync_api import expect, sync_playwright
from test_workstation import URL, open_mode


def exercise(browser, question, variable, compare=False, mobile=False):
    width = 390 if mobile else 1440
    page = browser.new_page(viewport={"width": width, "height": 900}, has_touch=mobile, is_mobile=mobile)
    errors, requests = [], []
    is_analysis = lambda url: re.search(r"/api/(trend|compare)(?:\?|$)", url)
    page.on("pageerror", lambda error: errors.append(str(error)))
    coverage = []
    page.on("request", lambda req: requests.append(req) if is_analysis(req.url) else
            coverage.append(req) if req.url.endswith("/api/coverage") else None)
    page.goto(URL, wait_until="domcontentloaded")
    open_mode(page, "Детектив")
    wizard = page.locator("#detective-workspace")
    parameters = page.get_by_role("region", name="Параметры исследования детектива")
    card = wizard.get_by_role("button", name=question, exact=True)
    next_step = page.get_by_role("button", name="Далее", exact=True)
    back = page.get_by_role("button", name="Назад", exact=True)
    if mobile:
        card.tap()
    else:
        card.focus()
        expect(card).to_be_focused()
        card.press("Enter")
    world = wizard.locator(".leaflet-container:visible")
    expect(world.locator(".leaflet-marker-icon")).to_have_count(2 if compare else 1)
    latitude = parameters.get_by_label("Широта", exact=True)
    before = latitude.input_value()
    pick = world.tap if mobile else world.click
    pick(position={"x": 100, "y": 112})
    expect(latitude).not_to_have_value(before)
    changed = latitude.input_value()
    next_step.click()
    back.click()
    expect(latitude).to_have_value(changed)
    parameters.get_by_role("button", name="Бишкек, Кыргызстан").click()
    back.click()
    expect(card).to_be_visible()
    card.click()
    next_step.click()
    start = parameters.get_by_label("Первый год", exact=True)
    start.fill("1991")
    back.click()
    next_step.click()
    expect(start).to_have_value("1991")
    start.fill("1990")
    assert not requests
    submit = parameters.get_by_role("button", name="Провести анализ", exact=True)
    expect(submit).to_be_enabled()
    pattern = "**/api/compare" if compare else "**/api/trend?*"
    if mobile or compare:
        page.route(pattern, lambda route: route.abort())
        submit.click()
        expect(wizard.get_by_text("Не удалось связаться с сервером EarthScope", exact=False)).to_be_visible()
        page.unroute(pattern)
        submit = wizard.get_by_role("button", name="Повторить запрос")
    with page.expect_response(lambda response: is_analysis(response.url), timeout=45000) as reply:
        submit.click()
    assert reply.value.ok
    body = reply.value.json()
    point = body["point_a"] if compare else body
    assert point["variable"] == variable and point["source"]["dataset"] == "MERRA2"
    assert (point["start_year"], point["end_year"]) == (1990, 2025)
    expect(wizard.get_by_role("heading", name="Что удалось выяснить")).to_be_visible()
    expect(wizard.locator(".js-plotly-plot").first).to_be_visible()
    text = wizard.inner_text()
    assert "доверительный интервал" in text and "за десятилетие" in text
    assert "NASA POWER" in text
    if compare:
        assert "Разность описательная" in text and body["point_b"]["latitude"] == 64.147
    expected_requests = 2 if mobile or compare else 1
    assert len(requests) == expected_requests
    open_mode(page, "Одна точка")
    open_mode(page, "Детектив")
    expect(wizard.get_by_role("heading", name="Что удалось выяснить")).to_be_visible()
    back.click()
    parameters.get_by_role("button", name="Открыть сохранённый ответ").click()
    expect(wizard.get_by_role("heading", name="Что удалось выяснить")).to_be_visible()
    assert len(requests) == expected_requests and len(coverage) <= 2
    if not mobile and not compare:
        # Fixture for a valid API response with no complete annual values.
        empty = {**body, "trend": None, "series": [
            {**year, "value": None, "fitted": None, "valid_days": 0}
            for year in body["series"]
        ]}
        page.route(pattern, lambda route: route.fulfill(json=empty))
        back.click()
        parameters.get_by_role("button", name="Провести анализ", exact=True).click()
        expect(wizard.get_by_text("Недостаточно данных для тренда: полных лет — 0", exact=False)).to_be_visible()
        assert "статистически значимый тренд" not in wizard.inner_text()
    assert page.evaluate("width => document.documentElement.scrollWidth <= width", width)
    assert not errors, errors
    page.close()


def exercise_reset(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Deliberately let the old response arrive even after the app aborts.
    page.add_init_script("""(() => {
        const fetchReal = window.fetch.bind(window)
        window.fetch = (url, options) => {
            if (typeof url === 'string' && url.includes('/api/trend?') && window.holdOld) {
                window.holdOld = false
                const { signal, ...rest } = options
                window.oldSignal = signal
                return fetchReal(url, rest).then(response => {
                    const json = response.json.bind(response)
                    response.json = async () => {
                        const result = await json()
                        window.oldBodyRead = true
                        return result
                    }
                    return response
                })
            }
            return fetchReal(url, options)
        }
    })()""")
    page.goto(URL, wait_until="domcontentloaded")
    ordinary = page.locator("#workspace")
    page.get_by_label("Первый год", exact=True).filter(visible=True).fill("1993")
    open_mode(page, "Детектив")
    wizard = page.locator("#detective-workspace")
    parameters = page.get_by_role("region", name="Параметры исследования детектива")
    def choose(name):
        scope = parameters if name in ("Далее", "Провести анализ", "Открыть сохранённый ответ") else wizard
        scope.get_by_role("button", name=name, exact=True).click()
    new = wizard.get_by_role("button", name="Новое исследование", exact=True)
    answer = wizard.get_by_role("heading", name="Что удалось выяснить")
    start = parameters.get_by_label("Первый год", exact=True)
    choose("Сравнение регионов")
    parameters.get_by_label("Переменная сравнения").select_option("precipitation")
    for label in ("A", "B"):
        parameters.get_by_role("radio", name=re.compile(f"^{label}:")).check()
        wizard.locator(".leaflet-container:visible").click(position={"x": 180, "y": 145})
    choose("Далее")
    start.fill("1991")
    new.click()
    expect(answer).to_have_count(0)
    choose("Сравнение регионов")
    expect(parameters.get_by_label("Переменная сравнения")).to_have_value("temperature")
    expect(parameters.get_by_role("radio", name=re.compile(r"^A: 42,870.*74,600"))).to_be_checked()
    expect(parameters.get_by_role("radio", name=re.compile(r"^B: 64,147.*21,942"))).to_be_visible()
    choose("Далее")
    expect(start).to_have_value("1990")
    expect(parameters.get_by_label("Последний год", exact=True)).to_have_value("2025")
    new.click()

    choose("Изменение температуры")
    choose("Далее")
    old_url = re.compile(r"/api/trend\?.*variable=temperature")
    held = []
    page.evaluate("window.holdOld = true")
    page.route(old_url, lambda route: held.append(route))
    choose("Провести анализ")
    expect(new).to_be_enabled()
    page.wait_for_function("() => window.oldSignal instanceof AbortSignal")
    new.click()
    assert page.evaluate("window.oldSignal.aborted")
    expect(answer).to_have_count(0)

    choose("Изменение осадков")
    choose("Далее")
    with page.expect_response(lambda response: "variable=precipitation" in response.url) as rain:
        choose("Провести анализ")
    assert rain.value.ok
    expect(answer).to_be_visible()
    expect(wizard.get_by_role("region", name="Ответ детектива")).to_contain_text("Осадки")
    assert len(held) == 1
    late_url = held[0].request.url
    real_temp = page.request.get(late_url)
    assert real_temp.ok
    with page.expect_response(old_url) as late:
        held[0].fulfill(json=real_temp.json())
    assert late.value.ok
    page.wait_for_function("() => window.oldBodyRead === true", timeout=5000)
    expect(wizard.get_by_role("region", name="Ответ детектива")).to_contain_text("Осадки")
    assert "среднегодовая температура" not in wizard.inner_text()
    wizard.get_by_role("button", name="Назад", exact=True).click()
    wizard.get_by_role("button", name="Назад", exact=True).click()
    wizard.get_by_role("button", name="Назад", exact=True).click()
    choose("Изменение температуры")
    choose("Далее")
    expect(parameters.get_by_role("button", name="Открыть сохранённый ответ")).to_have_count(0)
    wizard.get_by_role("button", name="Назад", exact=True).click()
    wizard.get_by_role("button", name="Назад", exact=True).click()
    choose("Изменение осадков")
    choose("Далее")
    choose("Открыть сохранённый ответ")
    with page.expect_response(lambda response: "variable=precipitation" in response.url) as repeated:
        choose("Повторить анализ")
    assert repeated.value.ok
    expect(answer).to_be_visible()
    # The next success also clears all saved result state.
    new.click()
    expect(answer).to_have_count(0)
    choose("Изменение осадков")
    choose("Далее")
    expect(parameters.get_by_role("button", name="Открыть сохранённый ответ")).to_have_count(0)
    page.route(re.compile(r"/api/trend\?.*variable=precipitation"), lambda route: route.abort())
    choose("Провести анализ")
    expect(wizard.get_by_text("Не удалось связаться с сервером EarthScope", exact=False)).to_be_visible()
    new.click()
    choose("Изменение осадков")
    choose("Далее")
    expect(wizard.get_by_role("button", name="Повторить запрос")).to_have_count(0)
    open_mode(page, "Одна точка")
    expect(ordinary).to_be_visible()
    expect(page.get_by_label("Первый год", exact=True).filter(visible=True)).to_have_value("1993")
    assert not errors, errors
    page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("EARTHSCOPE_BROWSER_EXECUTABLE"))
        exercise(browser, "Изменение температуры", "temperature")
        exercise(browser, "Изменение осадков", "precipitation", mobile=True)
        exercise(browser, "Сравнение регионов", "temperature", compare=True)
        exercise_reset(browser)
    print("PASS: scenario cards, three NASA-backed runs, keyboard/mobile, repeat without reload, reset and late response protection")
