"""Frontend-only contract checks. All API values are SYNTHETIC test fixtures.

Run against Vite with Python Playwright and an installed Chromium/Edge browser.
No test fixture is written to the runtime data cache or represented as NASA data.
"""
import calendar
import csv
import json
import os
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from test_workstation import URL, open_mode


def fixture(query, empty=False):
    first, last = int(query['start_year'][0]), int(query['end_year'][0])
    series = []
    for year in range(first, last + 1):
        days = 366 if calendar.isleap(year) else 365
        row = dict(year=year, count=0, total_days=0, max_duration=0,
                   mean_duration=None, max_tmax=None, valid_days=days, expected_days=days)
        if year == 1991:
            row.update(count=None, total_days=None, max_duration=None,
                       valid_days=days - 1)
        if year == 2020 and not empty:
            row.update(count=1, total_days=5, max_duration=5, mean_duration=5, max_tmax=36)
        series.append(row)
    return dict(latitude=float(query['latitude'][0]), longitude=float(query['longitude'][0]),
                start_year=first, end_year=last, threshold=float(query['threshold'][0]),
                min_duration=int(query['min_duration'][0]),
                source={'name': 'SYNTHETIC TEST FIXTURE — not NASA observations', 'parameter': 'T2M_MAX', 'time_standard': 'UTC'},
                series=series, episodes=[] if empty else [dict(start='2020-12-30', end='2021-01-03', duration=5,
                    max_tmax=36, mean_tmax=34, max_excess=6)],
                trends={k: dict(per_decade=0.25, years_used=len(series)-1, residual_lag1=None)
                        for k in ['count', 'total_days', 'max_duration']}, warnings=['SYNTHETIC fixture warning'])


def exercise(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 960}, accept_downloads=True)
    requests, errors, held = [], [], []
    mode = {'value': 'normal'}
    page.on('pageerror', lambda e: errors.append(str(e)))

    def heatwaves(route):
        request = route.request
        requests.append(request)
        if request.headers.get('accept') == 'application/pdf':
            route.fulfill(status=200, content_type='application/pdf', body=b'%PDF-1.4\nSynthetic test PDF\n%%EOF',
                          headers={'Content-Disposition': 'attachment; filename="EarthScope_heatwaves.pdf"'})
        elif mode['value'] == 'hold':
            held.append(route)
        elif mode['value'] == 'error':
            route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': {'code': 'SOURCE_UNAVAILABLE', 'message': 'Synthetic source failure'}}))
        else:
            data = fixture(parse_qs(urlparse(request.url).query), empty=mode['value'] == 'empty')
            if mode['value'] == 'incomplete':
                for row in data['series']:
                    row.update(count=None, total_days=None, max_duration=None, mean_duration=None, max_tmax=None, valid_days=0)
                data['episodes'] = []
                data['trends'] = dict(count=None, total_days=None, max_duration=None)
            route.fulfill(status=200, content_type='application/json', body=json.dumps(data))

    page.route('**/api/coverage', lambda r: r.fulfill(json=dict(min_year=1981, max_year=2025, min_years=5)))
    page.route('**/api/heatwaves?*', heatwaves)
    page.goto(URL, wait_until='domcontentloaded')
    open_mode(page, "Погодные экстремумы")
    page.get_by_role('button', name='Волны жары', exact=True).focus()
    page.keyboard.press('Enter')
    scope = page.get_by_role('complementary', name='Параметры волн жары')
    result = page.locator('#extremes-workspace')
    submit = scope.get_by_role('button', name='Рассчитать волны жары', exact=True)
    assert not requests
    duration = scope.get_by_label('Минимум суток подряд', exact=True)
    for invalid in ['', '0', '2.5', '-1']:
        duration.fill(invalid)
        expect(submit).to_be_disabled()
    duration.fill('3')
    submit.focus()
    page.keyboard.press('Enter')
    selector = result.get_by_label('Год для точных значений (UTC)', exact=True)
    expect(selector).to_be_visible()
    selector.select_option('2020')
    expect(result.get_by_role('heading', name='Эпизоды за 2020 · 1')).to_be_visible()
    assert len(requests) == 1
    assert parse_qs(urlparse(requests[0].url).query)['min_duration'] == ['3']
    expect(result.get_by_text('2020-12-30', exact=True)).to_be_visible()
    expect(result.get_by_text('2021-01-03', exact=True)).to_be_visible()
    expect(result.get_by_text('SYNTHETIC fixture warning', exact=True)).to_be_visible()
    assert result.locator('svg[role="img"]').count() == 3
    selector.select_option('1990')
    selector.focus()
    page.keyboard.press('ArrowDown')
    expect(selector).to_have_value('1991')
    expect(result.get_by_role('status').filter(has_text='1991:')).to_contain_text('Неполный год')
    selector.select_option('2021')
    expect(result.get_by_role('status').filter(has_text='2021:')).to_contain_text('0 эпизодов')
    for name, count in [('CSV · годы', 36), ('CSV · эпизоды', 1)]:
        with page.expect_download() as output:
            result.get_by_role('button', name=name, exact=True).click()
        with output.value.path().open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == count
        assert rows[0]['time_standard'] == 'UTC'
    assert rows[0]['start'] == '2020-12-30' and rows[0]['duration'] == '5'
    scope.get_by_label('Порог Tmax, °C', exact=True).fill('31')
    with page.expect_download():
        result.get_by_role('button', name='Сформировать PDF-отчёт', exact=True).click()
    assert requests[-1].headers['accept'] == 'application/pdf'
    assert parse_qs(urlparse(requests[-1].url).query)['threshold'] == ['30']  # saved, not draft
    mode['value'] = 'error'
    submit.click()
    expect(result.get_by_role('alert')).to_be_visible()
    expect(result.get_by_role('heading', name='По годам начала эпизодов')).to_be_visible()  # previous result retained
    mode['value'] = 'empty'
    result.get_by_role('button', name='Повторить с текущими параметрами').click()
    expect(result.get_by_text('В доступных наблюдениях за выбранный год эпизоды не обнаружены.', exact=True)).to_be_visible()
    mode['value'] = 'incomplete'
    submit.click()
    expect(result.get_by_text('Нет полных лет для годовых сравнений.', exact=True)).to_be_visible()
    mode['value'] = 'hold'
    submit.click()
    expect(scope.get_by_role('button', name='Загрузка…', exact=True)).to_be_disabled()
    scope.get_by_role('button', name='Отменить запрос').click()
    expect(result.get_by_text('Запрос отменён.', exact=False)).to_be_visible()
    for route in held:
        try:
            route.abort()
        except Exception:
            pass  # Browser may have already aborted the request.
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    mode['value'] = 'normal'
    open_mode(page, "Детектив")
    wizard = page.locator('#detective-workspace')
    parameters = page.get_by_role('region', name='Параметры исследования детектива')
    wizard.get_by_role('button', name='Стали ли волны жары чаще и продолжительнее?', exact=True).click()
    parameters.get_by_role('button', name='Далее', exact=True).click()
    parameters.get_by_label('Минимум суток подряд', exact=True).fill('0')
    expect(parameters.get_by_role('button', name='Провести анализ', exact=True)).to_be_disabled()
    parameters.get_by_label('Минимум суток подряд', exact=True).fill('3')
    parameters.get_by_role('button', name='Провести анализ', exact=True).click()
    expect(wizard.get_by_role('heading', name='Что удалось выяснить', exact=True)).to_be_visible()
    wizard.get_by_label('Год для точных значений (UTC)', exact=True).select_option('2020')
    expect(wizard.get_by_role('heading', name='Эпизоды за 2020 · 1')).to_be_visible()
    with page.expect_download():
        wizard.get_by_role('button', name='Сформировать PDF-отчёт · Детектив', exact=True).click()
    assert requests[-1].headers['accept'] == 'application/pdf'
    assert requests[-1].headers['x-earthscope-report'] == 'detective'
    assert '/api/heatwaves?' in requests[-1].url
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors, errors
    page.close()


if __name__ == '__main__':
    with sync_playwright() as p:
        for width in [1440, 390]:
            browser = p.chromium.launch(executable_path=os.environ.get('EARTHSCOPE_BROWSER_EXECUTABLE', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'), headless=True)
            exercise(browser, width)
            print(f'PASS heatwaves frontend contract, keyboard, downloads, states, Detective at {width}px (synthetic fixtures)')
            browser.close()
