"""Region card layout checks; requires Playwright and a running local server."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--browser', default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    errors = []
    payload = {
        'all': {'area_id': '', 'display_name': 'Все', 'button_count': 105, 'active_button_count': 105},
        'items': [{'area_id': str(index), 'display_name': name, 'button_count': count, 'active_button_count': count}
                  for index, (name, count) in enumerate([
                      ('MSK', 29), ('MSK-Alcon', 9), ('Дубна', 14), ('Санкт-Петербург', 53),
                      ('Большой регион с длинным названием', 999), ('Тест большого количества', 99999)])],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        context = browser.new_context()
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/areas', lambda route: route.fulfill(json=payload))
        page.goto(args.url)
        page.locator('.region-card').first.wait_for()
        page.evaluate('document.fonts.ready')
        for width in (320, 390, 768, 1024, 1280, 1440, 2220, 3840):
            page.set_viewport_size({'width': width, 'height': 1008})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'), width
            assert page.locator('.region-card').evaluate_all('''cards => cards.every(card => {
                const box = card.getBoundingClientRect();
                const count = card.querySelector('.region-count');
                const range = document.createRange();
                range.selectNodeContents(count);
                const text = range.getBoundingClientRect();
                const label = card.querySelector('.region-label').getBoundingClientRect();
                return text.left >= box.left && text.right <= box.right && text.bottom <= label.top + 1
                    && count.scrollWidth <= count.clientWidth + 1;
            })'''), (width, page.locator('.region-count').evaluate_all('''nodes => nodes.map(node => ({
                text:node.textContent,font:getComputedStyle(node).fontSize,width:node.clientWidth,scroll:node.scrollWidth,
                bottom:node.getBoundingClientRect().bottom,label:node.nextElementSibling.getBoundingClientRect().top
            }))'''))
            assert page.locator('.region-card').first.locator('.region-count').inner_text() == '105/105'
            assert page.locator('.region-count').evaluate_all('''nodes => {
                const sizes = nodes.map(node => parseFloat(getComputedStyle(node).fontSize));
                return Math.max(...sizes) - Math.min(...sizes) < .1;
            }'''), width
            if args.output and width in (390, 2220):
                output = Path(args.output)
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'regions-{width}.png'), full_page=True)
        payload['all'].update(button_count=99999, active_button_count=99998)
        page.evaluate('loadRegions()')
        page.wait_for_function('document.querySelector(".region-count").textContent === "99998/99999"')
        assert page.locator('.region-count').evaluate_all('''nodes => {
            const sizes = nodes.map(node => parseFloat(getComputedStyle(node).fontSize));
            return Math.max(...sizes) - Math.min(...sizes) < .1;
        }''')
        download = page.locator('.download-button')
        page.mouse.move(0, 0)
        page.wait_for_timeout(220)
        before = download.evaluate('node => getComputedStyle(node).borderTopColor')
        download.hover()
        page.wait_for_timeout(220)
        assert download.evaluate('node => getComputedStyle(node).borderTopColor') != before
        assert download.get_attribute('href') == '/download-app'
        assert not errors, errors
        browser.close()
    print('Region counters share one size at 320-3840px; large counts fit; download hover and refresh OK.')


if __name__ == '__main__':
    main()
