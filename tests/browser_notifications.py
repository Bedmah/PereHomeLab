"""Local-only notification expiry checks; server acknowledgements are intercepted."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--browser')
    parser.add_argument('--output')
    args = parser.parse_args()
    acknowledgements = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        context.add_init_script('HTMLMediaElement.prototype.play = () => Promise.resolve()')
        context.route('**/api/acknowledge', lambda route: (acknowledgements.append(route.request.url), route.abort()))
        page = context.new_page()
        page.goto(args.url + '/monitor')
        page.wait_for_function('clientConfigReady && soundEnabled')
        assert page.locator('#notificationMinutes').input_value() == '0'
        assert page.locator('#notificationTimerBadge').inner_text() == 'выкл'
        page.locator('#volumeToggleButton').click()
        assert page.locator('#settingsBackdrop').is_visible()
        assert page.locator('#settingsBackdrop').evaluate('node=>getComputedStyle(node).backdropFilter') == 'blur(7px)'
        assert page.evaluate('''() => {
            settingsBackdropAnimation.currentTime=100;
            const opacity=Number(getComputedStyle(settingsBackdrop).opacity);
            return opacity>0 && opacity<1 && settingsBackdropAnimation.effect.getKeyframes().every(frame=>!('backdropFilter' in frame));
        }'''), 'Backdrop must fade without animating blur radius'
        page.evaluate('setVolumePanelOpen(false); setVolumePanelOpen(true); setVolumePanelOpen(false); setVolumePanelOpen(true)')
        page.wait_for_timeout(250)
        assert page.locator('#volumePanel').is_visible()
        page.keyboard.press('Escape')
        page.wait_for_function('volumePanel.classList.contains("hidden")')
        assert not page.locator('#settingsBackdrop').is_visible()
        page.locator('#volumeToggleButton').click()
        page.locator('#settingsBackdrop').click(position={'x':10,'y':10})
        page.wait_for_function('volumePanel.classList.contains("hidden")')
        page.emulate_media(reduced_motion='reduce')
        page.locator('#volumeToggleButton').click()
        assert page.locator('#volumePanel').evaluate('node=>node.getAnimations().length') == 0
        page.locator('#volumeCloseButton').click()
        assert not page.locator('#volumePanel').is_visible()
        page.emulate_media(reduced_motion='no-preference')
        page.locator('#volumeToggleButton').click()
        page.locator('#volumeSlider').fill('37')
        page.locator('#volumeSlider').dispatch_event('input')
        assert page.locator('#volumeInput').input_value() == '37'
        assert page.locator('#volumeSlider').evaluate('node=>node.style.getPropertyValue("--volume-fill")') == '37%'
        page.locator('#volumeSlider').press('ArrowRight')
        assert page.locator('#volumeInput').input_value() == '38'
        page.locator('#volumeInput').fill('73')
        assert page.locator('#volumeSlider').input_value() == '73'
        assert page.evaluate('''() => {
            const modals=[...document.querySelectorAll('.details-modal.hidden')];
            modals.forEach(node=>node.classList.remove('hidden'));
            const centered=[...document.querySelectorAll('.details-close')].every(node=>{
                const button=node.getBoundingClientRect(), icon=node.querySelector('svg').getBoundingClientRect();
                return button.width>0 && Math.abs((button.left+button.right-icon.left-icon.right)/2)<1
                    && Math.abs((button.top+button.bottom-icon.top-icon.bottom)/2)<1;
            });
            modals.forEach(node=>node.classList.add('hidden'));
            return centered;
        }'''), 'Close icons must be centered in every dialog'
        page.locator('#volumeCloseButton').click()
        assert page.evaluate('''() => {
            addAlerts([{id:'expiry-default',device_name:'Default'}]);
            notificationSettings.seen['expiry-default']=Date.now()-3600000;
            expireLocalAlerts();
            return activeAlerts.length===1;
        }'''), 'Default must never expire'
        assert page.locator('.alert-countdown:visible').count() == 0
        page.evaluate('removeAcknowledgedAlerts(["expiry-default"])')
        page.locator('#volumeToggleButton').click()
        page.locator('#notificationMinutes').fill('5')
        page.locator('#notificationMinutes').dispatch_event('change')
        assert page.locator('#notificationTimerBadge').inner_text() == '5 мин.'
        page.evaluate('''() => {
            addAlerts([{id:'countdown-a',device_name:'First'},{id:'countdown-b',device_name:'Second'}]);
            notificationSettings.seen['countdown-a']=Date.now()-60000;
            notificationSettings.seen['countdown-b']=Date.now()-120000;
            updateNotificationCountdowns();
            window.originalCountdownRow=popupList.querySelector('.press');
        }''')
        texts = page.locator('.alert-countdown').all_text_contents()
        assert len(set(texts)) == 2
        initial_text = page.locator('.alert-countdown').first.inner_text()
        page.wait_for_function('(text)=>popupList.querySelector(".alert-countdown").textContent!==text', arg=initial_text)
        assert page.evaluate('window.originalCountdownRow===popupList.querySelector(".press")'), 'Tick must not rebuild popup'
        page.evaluate('removeAcknowledgedAlerts(["countdown-a","countdown-b"])')
        assert page.evaluate('''() => {
            addAlerts([{id:'expiry-old',device_name:'Old'},{id:'expiry-new',device_name:'New'}]);
            notificationSettings.seen['expiry-old']=Date.now()-300001;
            notificationSettings.seen['expiry-new']=Date.now()-120000;
            saveNotificationSettings();
            expireLocalAlerts();
            return activeAlerts.length===1 && activeAlerts[0].id==='expiry-new' && soundAlertId==='expiry-new';
        }'''), 'Each event needs its own deadline and independent sound'
        first_seen = page.evaluate('notificationSettings.seen["expiry-new"]')
        page.reload()
        page.wait_for_function('clientConfigReady && soundEnabled')
        assert page.locator('#notificationMinutes').input_value() == '5'
        assert page.locator('#notificationTimerBadge').inner_text() == '5 мин.'
        assert page.evaluate('notificationSettings.seen["expiry-new"]') == first_seen
        page.evaluate('addAlerts([{id:"expiry-old",device_name:"Old"},{id:"expiry-new",device_name:"New"}])')
        assert page.evaluate('activeAlerts.length===1 && activeAlerts[0].id==="expiry-new"')
        page.evaluate('notificationSettings.seen["expiry-new"]=Date.now()-300001')
        page.wait_for_function('activeAlerts.length===0 && !soundLoopActive')
        page.reload()
        page.wait_for_function('clientConfigReady')
        page.evaluate('addAlerts([{id:"expiry-new",device_name:"New"}])')
        assert page.evaluate('activeAlerts.length===0'), 'Expired call must not return after reload'
        other = context.new_page()
        other.goto(args.url + '/spb')
        other.wait_for_function('clientConfigReady')
        assert other.locator('#notificationMinutes').input_value() == '0'
        independent = browser.new_context()
        isolated_page = independent.new_page()
        isolated_page.goto(args.url + '/monitor')
        isolated_page.wait_for_function('clientConfigReady')
        assert isolated_page.locator('#notificationMinutes').input_value() == '0'
        isolated_page.evaluate('addAlerts([{id:"expiry-old",device_name:"Old"}])')
        assert isolated_page.evaluate('activeAlerts.length===1'), 'Other browser must still display the call'
        page.locator('#volumeToggleButton').click()
        for width, height in ((1280, 800), (480, 540), (320, 600)):
            page.set_viewport_size({'width': width, 'height': height})
            assert page.locator('#volumePanel').evaluate('node => { const rect=node.getBoundingClientRect(); return rect.left>=0 && rect.right<=innerWidth+1 && rect.top>=0 && rect.bottom<=innerHeight; }')
        if args.output:
            page.screenshot(path=str(Path(args.output)))
            page.set_viewport_size({'width':1280,'height':900})
            page.locator('#volumeCloseButton').click()
            page.evaluate('''addAlerts([{id:'preview-a',device_name:'Room A',area_id:'spb',area_name:'SPB'},
                {id:'preview-b',device_name:'Room B',area_id:'msk',area_name:'MSK'}])''')
            assert page.locator('.alert-countdown:visible').count() == 2
            assert page.locator('.press .area-badge').evaluate_all('nodes=>nodes.every(node=>node.getBoundingClientRect().width<node.parentElement.getBoundingClientRect().width-20)'), 'Short region badges must not fill the card'
            assert page.locator('.press').evaluate_all('nodes=>nodes.every(node=>node.querySelector(".alert-countdown").getBoundingClientRect().bottom<=node.getBoundingClientRect().bottom)')
            page.screenshot(path=str(Path(args.output).with_stem('notification-countdowns')))
        assert not acknowledgements, 'Local expiry must not acknowledge anything on server'
        browser.close()
    print('Notification defaults, independent timers, reload persistence, browser/region isolation, sound and settings layout: OK')


if __name__ == '__main__':
    main()
