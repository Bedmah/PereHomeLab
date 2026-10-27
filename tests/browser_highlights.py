"""History highlight settings and live assignment regression checks."""

import argparse
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--browser')
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        page = browser.new_page(viewport={'width':1280,'height':900})
        page.goto(args.url + '/monitor')
        page.wait_for_selector('.history-item')
        assert page.locator('#highlightMinutes').input_value() == '5'
        page.evaluate('''renderHistory([
            {id:'highlight-new',device_name:'New',pressed_at:new Date(Date.now()-1000).toISOString(),call_state:'waiting'},
            {id:'highlight-old',device_name:'Old',pressed_at:new Date(Date.now()-600000).toISOString(),call_state:'waiting'},
            {id:'highlight-taken',device_name:'Taken',pressed_at:new Date().toISOString(),call_state:'accepted'}])''')
        assert page.locator('.history-item.fresh').count() == 1
        assert page.locator('.history-item.accepted:not(.fresh)').count() == 1
        assert float(page.locator('.history-item.accepted').evaluate('node=>getComputedStyle(node).opacity')) < 1
        page.locator('#volumeToggleButton').click()
        page.locator('#highlightMinutes').fill('0')
        page.locator('#highlightMinutes').dispatch_event('change')
        assert page.locator('.history-item.fresh').count() == 2
        assert page.locator('#notificationMinutes').input_value() == '0'
        page.evaluate('applyCallChanges([{id:"highlight-old",revision:1,call_state:"accepted"}])')
        assert page.locator('[data-event-id="highlight-old"].accepted:not(.fresh)').count() == 1
        page.evaluate('applyCallChanges([{id:"highlight-old",revision:2,call_state:"waiting"}])')
        assert page.locator('[data-event-id="highlight-old"].fresh:not(.accepted)').count() == 1
        page.locator('#highlightMinutes').fill('1')
        page.locator('#highlightMinutes').dispatch_event('change')
        assert page.locator('.history-item.fresh').count() == 1
        page.evaluate('''window.originalHighlightRow=historyList.querySelector('[data-event-id="highlight-new"]');
            window.originalHighlightRow.dataset.pressedAt=new Date(Date.now()-60001).toISOString()''')
        page.wait_for_function('!window.originalHighlightRow.classList.contains("fresh")')
        assert page.evaluate('window.originalHighlightRow===historyList.querySelector(".history-item")')
        page.reload()
        page.wait_for_function('clientConfigReady')
        assert page.locator('#highlightMinutes').input_value() == '1'
        page.goto(args.url + '/spb')
        page.wait_for_function('clientConfigReady')
        assert page.locator('#highlightMinutes').input_value() == '5'
        browser.close()
    print('Highlight default, expiry, unlimited mode, assignment/release, dimming, DOM stability and persistence: OK')


if __name__ == '__main__':
    main()
