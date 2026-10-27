"""Battery dialog semantics and responsive layout checks; no device writes."""

import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--browser')
    parser.add_argument('--output')
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        page = browser.new_page(viewport={'width':1280,'height':900})
        page.goto(args.url + '/monitor')
        page.wait_for_selector('.battery-item')
        page.locator('.battery-item').first.click()
        assert page.locator('#batteryModal').is_visible()
        assert page.locator('#batteryDetailsUpdated').is_visible()
        assert page.locator('#batteryModal').evaluate('node=>getComputedStyle(node).backdropFilter') == 'blur(7px)'
        assert page.locator('#batteryModal dt').count() == 2
        for percent, level in ((0,'critical'),(15,'critical'),(30,'low'),(80,'normal'),(None,'unknown')):
            page.evaluate('''item=>openBatteryDetails({name:'Переговорная с очень длинным названием для проверки отображения',
                area_name:'Санкт-Петербург',received_at:'2026-10-26T16:00:00+03:00',
                updated_at:'2026-06-19T10:00:00+03:00',...item})''', {'percent':percent})
            assert page.locator('#batteryChargeSummary').get_attribute('data-level') == level
            assert page.locator('#batteryDetailsPercent').inner_text() == ('—' if percent is None else f'{percent}%')
            assert page.locator('#batteryDetailsUpdated').is_visible()
            assert 'HA давно' not in page.locator('#batteryDetailsStatus').inner_text()
        for width, height in ((1920,1080),(1280,900),(640,720),(480,540),(320,600)):
            page.set_viewport_size({'width':width,'height':height})
            assert page.locator('.battery-details-card').evaluate('''node=>{
                const rect=node.getBoundingClientRect();
                return rect.left>=0 && rect.right<=innerWidth+1 && rect.top>=0 && rect.bottom<=innerHeight+1
                    && node.scrollWidth<=node.clientWidth+1;
            }'''), f'Battery dialog overflow at {width}x{height}'
        if args.output:
            page.set_viewport_size({'width':1280,'height':900})
            page.evaluate("openBatteryDetails({name:'Переговорная SP4.04',area_name:'SPB',percent:15,received_at:'2026-10-26T16:00:00+03:00',updated_at:'2026-06-19T10:00:00+03:00'})")
            page.screenshot(path=str(Path(args.output)))
        page.locator('#batteryCloseButton').click()
        assert not page.locator('#batteryModal').is_visible()
        browser.close()
    print('Battery details: known/zero/unknown charge, always visible dates, backdrop blur, close and narrow screens OK')


if __name__ == '__main__':
    main()
