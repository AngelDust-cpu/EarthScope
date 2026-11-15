"""Isolated simulated PDF integration checks; requires Vite on EARTHSCOPE_TEST_URL."""
import os
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get('EARTHSCOPE_TEST_URL', 'http://127.0.0.1:5173')


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get(
            'EARTHSCOPE_BROWSER_EXECUTABLE', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
            headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844}, accept_downloads=True)
        requests = []
        pending_routes = []
        mode = ['ok']

        def report(route):
            requests.append(route.request)
            if mode[0] == 'hold':
                pending_routes.append(route)
                return  # Leave pending until the cancellation button aborts it.
            if mode[0] == 'error':
                route.fulfill(status=503, content_type='application/json', body='{"error":{"message":"Report unavailable"}}')
            elif mode[0] == 'invalid':
                route.fulfill(content_type='application/json', body='{}')
            elif mode[0] == 'empty':
                route.fulfill(content_type='application/pdf', body='')
            else:
                route.fulfill(content_type='application/pdf', body=b'%PDF-1.4\n%%EOF',
                              headers={'Content-Disposition': 'attachment; filename="../../unsafe.exe.pdf"'})

        page.route('**/api/trend?**', report)
        page.goto(URL + '/tests/pdf-harness.html')
        button = page.get_by_role('button', name='Сформировать PDF-отчёт', exact=True)
        expect(button).to_be_visible()
        button.focus()
        with page.expect_download() as download:
            page.keyboard.press('Enter')
        assert download.value.suggested_filename.endswith('.pdf')
        assert '/' not in download.value.suggested_filename
        assert requests[-1].headers['accept'] == 'application/pdf'
        assert 'x-earthscope-report' not in requests[-1].headers
        assert page.evaluate('document.documentElement.scrollWidth <= 390')
        for state in ['error', 'invalid', 'empty']:
            mode[0] = state
            button.click()
            expect(page.get_by_role('alert')).not_to_be_empty()
            expect(button).to_be_enabled()
        mode[0] = 'hold'
        button.click()
        expect(page.get_by_role('button', name='Подготовка PDF…')).to_be_disabled()
        page.get_by_role('button', name='Отменить PDF').focus()
        page.keyboard.press('Space')
        expect(page.get_by_role('status')).to_have_text('Загрузка PDF отменена.')
        expect(button).to_be_enabled()
        for route in pending_routes:
            route.abort()
        mode[0] = 'ok'
        page.set_viewport_size({'width': 1440, 'height': 900})
        page.goto(URL + '/tests/pdf-harness.html?detective&post')
        with page.expect_download():
            page.get_by_role('button', name='Сформировать PDF-отчёт · Детектив').click()
        assert requests[-1].method == 'POST'
        assert requests[-1].headers['x-earthscope-report'] == 'detective'
        assert requests[-1].post_data_json['point_a']['latitude'] == 42
        assert page.evaluate('document.documentElement.scrollWidth <= 1440')
        # Pure request builders take result objects, with no dependency on forms.
        page.wait_for_function('!!window.analysisPdfRequest')
        page.evaluate('''() => {
          const data = { latitude: 42, longitude: 74, variable: 'temperature', start_year: 1990, end_year: 2025 };
          for (const [extra, route, parameter] of [
            [{}, 'trend', 'variable=temperature'],
            [{baseline_start_year: 1991, baseline_end_year: 2020}, 'anomalies', 'baseline_end_year=2020'],
            [{period: 'all'}, 'seasonal', 'period=all'],
            [{event: 'hot_days', threshold: 30}, 'extremes', 'threshold=30'],
          ]) {
            const request = window.analysisPdfRequest({...data, ...extra});
            if (!request.url.includes('/api/' + route + '?') || !request.url.includes(parameter)) throw Error(route);
          }
          const request = window.analysisPdfRequest({point_a: data, point_b: {...data, longitude: -21}});
          if (JSON.parse(request.body).point_b.longitude !== -21 || !request.url.endsWith('/api/compare')) throw Error('compare');
          if (!window.safePdfFilename("attachment; filename*=UTF-8''%2Fbad%00.pdf").match(/^[a-zA-Z0-9_-]+\\.pdf$/)) throw Error('filename');
        }''')
        browser.close()
        print('PASS: GET/POST, headers, result params, safe filenames, keyboard, 390px, loading, abort, errors, empty/non-PDF responses')


if __name__ == '__main__':
    main()
