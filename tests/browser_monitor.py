"""Browser regression checks; requires Playwright and a running local server."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--browser', default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        for initially_blocked in (True, False):
            context = browser.new_context(viewport={'width': 1440, 'height': 950})
            context.add_init_script('''
                window.monitorPlayCount = 0;
                HTMLMediaElement.prototype.play = function () {
                    window.monitorPlayCount++;
                    if (BLOCKED && window.monitorPlayCount === 1)
                        return Promise.reject(new DOMException('Autoplay blocked', 'NotAllowedError'));
                    return Promise.resolve();
                };
            '''.replace('BLOCKED', json.dumps(initially_blocked)))
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))

            def acknowledge(route):
                ids = route.request.post_data_json['ids']
                route.fulfill(json={'updated': len(ids), 'events': [
                    {'id': event_id, 'revision': 100, 'sound_silenced': True} for event_id in ids]})

            page.route('**/api/acknowledge', acknowledge)
            page.goto(args.url.rstrip('/') + '/monitor')
            page.wait_for_function('clientConfigReady && audioProbePromise === null && historyList.children.length > 0')
            if initially_blocked:
                assert page.locator('#enableSoundButton').get_attribute('aria-label') == 'Разрешить звук уведомлений'
                assert page.locator('#enableSoundButton use').get_attribute('href').endswith('#sound-off')
                page.evaluate('''addAlerts([{id:'browser-blocked',device_name:'Тест',area_id:'spb'}])''')
                assert page.evaluate('soundAlertId === null')
                page.locator('#popupTitle').click()
                page.wait_for_function('soundEnabled && soundAlertId === "browser-blocked"')
                page.locator('#clearPopupButton').click()
                page.wait_for_function('activeAlerts.length === 0 && !soundLoopActive')
            else:
                assert page.evaluate('soundEnabled')
                assert page.evaluate('monitorPlayCount') == 1
            print('Sound startup:', 'gesture fallback OK' if initially_blocked else 'automatic without click OK')
            assert page.locator('.battery-panel,.history-panel,.history-item,.battery-item,.nav-status,.volume-control').evaluate_all('''nodes =>
                nodes.every(node => getComputedStyle(node).backdropFilter === 'none')'''), 'Lists must not use costly blur'

            checks = page.evaluate('''async () => {
                const event = (id) => ({id, device_name:'Длинное название переговорки для проверки', area_id:'spb',
                    area_name:'Санкт-Петербург', pressed_at_display:'20.10.2026 15:00:00'});
                const sameAudioUrl = soundUrlForEvent(event('a')) === soundUrlForEvent(event('a'));
                addAlerts([event('browser-a'), event('browser-b')]);
                const firstOwnsSound = soundAlertId === 'browser-a';
                await testCurrentVolume();
                const testDoesNotInterrupt = soundAlertId === 'browser-a';
                applyCallChanges([{id:'browser-a', revision:1, sound_silenced:true}]);
                const nextOwnsSound = soundAlertId === 'browser-b';
                const originalRow = popupList.firstElementChild;
                for (let index=0; index<100; index++) applyCallChanges([{id:'browser-a', revision:1, sound_silenced:true}]);
                const noRedundantPopupRender = popupList.firstElementChild === originalRow;
                applyCallChanges([{id:'browser-b', revision:1, sound_silenced:true}]);
                const stopped = !soundLoopActive && activeAlerts.length === 0;
                applyCallChanges([{...event('browser-a'), revision:2, sound_silenced:false}], true);
                applyCallChanges([{id:'browser-a', revision:1, sound_silenced:true}]);
                const staleIgnored = soundAlertId === null && activeAlerts.length === 0 && callRevisions.get('browser-a') === 2;
                applyCallChanges([{id:'browser-a', revision:3, sound_silenced:true}]);
                const history = [{...event('browser-history'), pressed_at:'2026-10-20T15:00:00'}];
                renderHistory(history);
                const row = historyList.firstElementChild;
                renderHistory(history);
                const noRedundantHistoryRender = row === historyList.firstElementChild;
                history[0].call_state = 'accepted';
                history[0].assignee_short = 'corp.login';
                renderHistory(history);
                const corporateShown = historyList.querySelector('.history-assignee').textContent === 'Пошёл: corp.login';
                history[0].assignee_short = '@telegram_user';
                renderHistory(history);
                const telegramFallback = historyList.querySelector('.history-assignee').textContent === 'Пошёл: @telegram_user';
                history[0].call_state = 'waiting';
                renderHistory(history);
                const releasedHidden = !historyList.querySelector('.history-assignee');
                const start = performance.now();
                for (let index=0; index<1000; index++) applyCallChanges([{id:'browser-a', revision:3, sound_silenced:true}]);
                return {sameAudioUrl, firstOwnsSound, testDoesNotInterrupt, nextOwnsSound, noRedundantPopupRender,
                    stopped, staleIgnored, noRedundantHistoryRender, corporateShown, telegramFallback, releasedHidden,
                    repeatedStatusMs:performance.now()-start};
            }''')
            assert all(value for key, value in checks.items() if key != 'repeatedStatusMs'), checks
            print('Queue and unchanged DOM:', checks)

            ordering = page.evaluate('''() => {
                const event = (id, area, hour) => ({id,device_name:id,area_id:area,area_name:area,
                    pressed_at:`2026-10-26T${hour}:00:00`});
                addAlerts([event('order-old','spb','10'),event('order-middle','spb','12')]);
                const sameArea = [...popupList.querySelectorAll('.press .name')].map(node=>node.textContent);
                addAlerts([event('order-other','msk','13'),event('order-new','spb','14')]);
                const groups = [...popupList.querySelectorAll('.popup-region-group')];
                const newestGroupFirst = groups[0].querySelector('.popup-region-title').textContent.startsWith('spb');
                const newestInGroupFirst = groups[0].querySelector('.press .name').textContent==='order-new';
                const soundUnchanged = soundAlertId==='order-old';
                removeAcknowledgedAlerts(['order-old']);
                const soundStillFifo = soundAlertId==='order-middle';
                removeAcknowledgedAlerts(['order-middle','order-other','order-new']);
                addAlerts([{id:'order-no-time-a',device_name:'order-no-time-a'},
                           {id:'order-no-time-b',device_name:'order-no-time-b'}]);
                const missingTimes = popupList.querySelector('.press .name').textContent==='order-no-time-b';
                removeAcknowledgedAlerts(['order-no-time-a','order-no-time-b']);
                return {sameArea: sameArea.join(',')==='order-middle,order-old',newestGroupFirst,
                    newestInGroupFirst,soundUnchanged,soundStillFifo,missingTimes};
            }''')
            assert all(ordering.values()), ordering
            print('Popup newest-first ordering and independent FIFO sound: OK')

            race = page.evaluate('''async () => {
                let rejectOld;
                const original = alertAudio.play;
                alertAudio.play = () => new Promise((resolve, reject) => { rejectOld=reject; });
                addAlerts([{id:'race-a',device_name:'A'}]);
                alertAudio.play = () => Promise.resolve();
                addAlerts([{id:'race-b',device_name:'B'}]);
                applyCallChanges([{id:'race-a',revision:1,sound_silenced:true}]);
                rejectOld(new DOMException('Stale playback rejected','NotAllowedError'));
                await new Promise(resolve => setTimeout(resolve, 0));
                const result = soundEnabled && soundAlertId === 'race-b';
                applyCallChanges([{id:'race-b',revision:1,sound_silenced:true}]);
                alertAudio.play = original;
                return result;
            }''')
            assert race

            assignment = {'call_state': 'accepted', 'assignee_name': 'Иван Петров · i.petrov',
                          'assignee_source': 'telegram', 'assignee_source_label': 'Telegram', 'revision': 1,
                          'acknowledged_at':'2026-10-20T15:20:05','acknowledged_at_display':'20.10.2026 15:20:05'}
            page.route('**/api/history/browser-comments', lambda route: route.fulfill(json={'item': {
                **assignment,
                'id': 'browser-comments', 'comments': [
                    {'author_name': 'Иван Петров · i.petrov', 'created_at_display': '20.10.2026 15:30:05',
                     'created_at': '2026-10-20T15:30:05', 'body': 'Заменили кабель.\nСвязь восстановлена.'},
                    {'author_name': 'Тестовый сотрудник', 'created_at_display': '20.10.2026 15:31:00',
                     'body': '<script>window.badComment=true</script> ' + 'ОченьДлинныйТекст' * 40}],
                'actions': [{'action': 'takeover', 'actor_name': 'Не должен отображаться'}]}}))
            page.evaluate('openDetails({id:"browser-comments",device_name:"Переговорная с длинным названием",area_name:"Санкт-Петербург",pressed_at_display:"20.10.2026 15:00:00"})')
            page.locator('.comment-card').first.wait_for()
            assert page.locator('.comment-card').count() == 2
            assert page.locator('#assignmentName').inner_text() == 'Иван Петров · i.petrov'
            assert page.locator('#assignmentPlatform').inner_text() == 'Telegram'
            assert page.locator('#assignmentLabel').inner_text() == 'Пошёл'
            assert page.locator('#assignmentTime').inner_text() == '20.10.2026 15:20:05'
            assert not page.locator('#detailsModal').get_by_text('Кто принял', exact=True).count()
            assert not page.locator('#detailsModal').get_by_text('Не должен отображаться', exact=True).count()
            assert page.evaluate('window.badComment === undefined')
            page.evaluate('''renderAssignment({call_state:'accepted',assignee_details:'Иван Петров · i.petrov · Telegram: @test_user',assignee_source:'telegram'})''')
            assert page.locator('#assignmentName').inner_text().endswith('Telegram: @test_user')
            page.evaluate('''renderAssignment({call_state:'accepted',assignee_details:'Telegram: @test_user',assignee_source:'telegram'})''')
            assert page.locator('#assignmentName').inner_text() == 'Telegram: @test_user'
            assignment.update(assignee_name='Анна Иванова', assignee_source='web', assignee_source_label='Веб-интерфейс', revision=2,
                              acknowledged_at='2026-10-20T15:25:00',acknowledged_at_display='20.10.2026 15:25:00')
            page.evaluate('''handleServerMessage({type:'call_updated',action:'claim',events:[{
                id:'browser-comments',revision:2,call_state:'accepted',assignee_name:'Анна Иванова',
                assignee_source:'web',assignee_source_label:'Веб-интерфейс',sound_silenced:true,
                acknowledged_at:'2026-10-20T15:25:00',acknowledged_at_display:'20.10.2026 15:25:00'}]})''')
            page.wait_for_function('assignmentName.textContent === "Анна Иванова" && assignmentPlatform.textContent === "Веб-интерфейс"')
            page.wait_for_function('assignmentTime.textContent === "20.10.2026 15:25:00"')
            assignment.update(call_state='waiting', assignee_name=None, assignee_source=None, assignee_source_label=None, revision=3)
            page.evaluate('''handleServerMessage({type:'call_updated',action:'release',events:[{
                id:'browser-comments',revision:3,call_state:'waiting',device_name:'Тест',sound_silenced:false}]})''')
            page.wait_for_function('assignmentName.textContent === "Ожидает сотрудника" && assignmentPlatform.hidden && assignmentTime.hidden')
            page.evaluate('removeAcknowledgedAlerts(["browser-comments"])')
            assignment.update(call_state='accepted', assignee_name='Иван Петров · i.petrov', assignee_source='telegram', assignee_source_label='Telegram', revision=4)
            page.evaluate('''handleServerMessage({type:'call_updated',action:'claim',events:[{
                id:'browser-comments',revision:4,call_state:'accepted',assignee_name:'Иван Петров · i.petrov',
                assignee_source:'telegram',assignee_source_label:'Telegram',sound_silenced:true}]})''')
            page.wait_for_function('assignmentName.textContent === "Иван Петров · i.petrov"')
            print('Current assignee: live takeover, platform change, release and reassignment OK')
            page.evaluate('''renderHistory(Array.from({length:100},(_,index)=>({id:'layout-'+index,
                device_name:index%2 ? 'Очень длинное название переговорной комнаты для проверки сетки и переноса строк' : 'Кнопка 8',
                area_name:'Санкт-Петербург',pressed_at_display:'20.10.2026 15:00:00',call_state:index%3 ? 'accepted' : 'waiting',
                assignee_short:index%2 ? '@telegram_username_long' : 'corporate.login.with.long.name'})))''')
            for width, height in ((1920, 900), (1440, 900), (1280, 900), (1024, 1024), (960, 1080), (900, 900), (873, 982), (768, 900), (390, 844), (320, 600), (1280, 600), (800, 1280)):
                page.set_viewport_size({'width': width, 'height': height})
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
                assert page.locator('.call-details-card').evaluate('''node => {
                    const rect=node.getBoundingClientRect();
                    return rect.left>=0 && rect.right<=innerWidth+1 && rect.top>=0 && rect.bottom<=innerHeight+1
                        && node.scrollWidth<=node.clientWidth+1;
                }'''), f'Call details overflow at {width}x{height}'
                assert page.locator('#detailsCloseButton').evaluate('node=>node.getBoundingClientRect().right<=innerWidth'), 'Close button outside viewport'
                assert page.locator('#callComments').evaluate('(node) => node.scrollWidth <= node.clientWidth + 1')
                assert page.locator('.history-item').evaluate_all('''nodes => nodes.every(node => {
                    const row=node.getBoundingClientRect();
                    return [...node.querySelectorAll('.name,.time,.history-assignee')].every(child => {
                        const rect=child.getBoundingClientRect();
                        return rect.bottom<=row.bottom+1 && rect.right<=row.right+1;
                    });
                })'''), 'History card text overflow'
                assert page.locator('.history-item').evaluate_all('''nodes => nodes.every((node,index) => {
                    const current=node.getBoundingClientRect();
                    return nodes.slice(index+1).every(other => {
                        const next=other.getBoundingClientRect();
                        return next.left>=current.right-1 || next.right<=current.left+1 || next.top>=current.bottom-1;
                    });
                })'''), 'History cards overlap'
                assert page.locator('.history-item').evaluate_all('''nodes => {
                    const rects=nodes.map(node=>node.getBoundingClientRect());
                    return rects.every(rect=>rects.every(other=>Math.abs(rect.top-other.top)>1 || Math.abs(rect.height-other.height)<1));
                }'''), 'History cards in the same row have different heights'
                if args.output:
                    output = Path(args.output)
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(output / f'comments-{width}.png'))
            page.evaluate("renderHistory(Array.from({length:5},(_,index)=>({id:'compact-'+index,device_name:'R13104',pressed_at_display:'23.10.2026 11:33:25',call_state:'waiting'})))")
            assert page.locator('.history-item').evaluate_all('nodes => nodes.every(node => node.getBoundingClientRect().height < 120)'), 'Empty space reserved for missing assignees'
            page.set_viewport_size({'width': 1920, 'height': 1080})
            page.evaluate("renderHistory(Array.from({length:12},(_,index)=>({id:'single-assignee-'+index,device_name:'R13104',area_name:'MSK',pressed_at_display:'23.10.2026 11:33:25',call_state:index===0?'accepted':'waiting',assignee_short:'test.employee'})))")
            assert page.locator('.history-item').evaluate_all('''nodes => {
                const rects=nodes.map(node=>node.getBoundingClientRect());
                return rects.every(rect=>rects.every(other=>Math.abs(rect.top-other.top)>1 || Math.abs(rect.height-other.height)<1))
                    && rects[0].height>rects[rects.length-1].height
                    && rects.every((rect,index)=>!index || rect.top>=rects[index-1].bottom-1 || Math.abs(rect.top-rects[index-1].top)<1);
            }'''), 'One assignee should expand only its own row without overlap'
            page.locator('#detailsCloseButton').click()
            for width, height in ((1920,1080),(1200,800),(1100,900),(1067,900),(960,1080),(901,900),(900,900),(873,982),(800,800),(640,720),(480,540),(390,844),(320,600)):
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('''() => {
                    const history=document.querySelector('.history-panel').getBoundingClientRect();
                    const battery=document.querySelector('.battery-panel').getBoundingClientRect();
                    const hero=document.querySelector('.hero').getBoundingClientRect();
                    const copy=document.querySelector('.hero-copy').getBoundingClientRect();
                    const metrics=document.querySelector('.hero-metrics').getBoundingClientRect();
                    const separate=history.bottom<=battery.top || battery.bottom<=history.top || history.left>=battery.right || battery.left>=history.right;
                    const headerSeparate=copy.bottom<=metrics.top+1 || metrics.bottom<=copy.top+1 || copy.left>=metrics.right-1 || metrics.left>=copy.right-1;
                    return separate && headerSeparate && (innerWidth>1100 || hero.height<360) && Math.min(history.top,battery.top)>=hero.bottom-1
                        && (innerWidth>900 ? battery.right<=history.left
                            : (history.bottom<=battery.top && history.height>=battery.height))
                        && document.documentElement.scrollWidth<=innerWidth+1;
                }'''), f'Dashboard overlap or wrong panel priority at {width}x{height}'
                assert page.locator('#historyList').evaluate('(node)=>node.clientHeight>=120'), 'History has no usable space'
                if args.output:
                    page.screenshot(path=str(Path(args.output)/f'dashboard-{width}-{height}.png'),full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 720})
            page.evaluate('addAlerts([{id:"button-style",device_name:"Проверка уведомления",area_name:"Москва"}])')
            colors = page.locator('#clearPopupButton').evaluate('(node) => ({bg:getComputedStyle(node).backgroundColor,fg:getComputedStyle(node).color})')
            assert colors['bg'] == 'rgb(96, 165, 250)' and colors['fg'] == 'rgb(7, 21, 38)', colors
            if args.output:
                page.screenshot(path=str(Path(args.output) / 'notification-button.png'))
            page.evaluate('removeAcknowledgedAlerts(["button-style"]); applyVolume(57)')
            page.reload()
            page.wait_for_function('clientConfigReady && audioProbePromise === null')
            assert page.locator('#volumeInput').input_value() == '57'
            print('Comments, XSS safety, modal layouts, button contrast, saved volume: OK')
            context.close()
        browser.close()
        kiosk = playwright.chromium.launch(headless=True, executable_path=args.browser,
                                          args=['--autoplay-policy=no-user-gesture-required'])
        context = kiosk.new_context(viewport={'width': 1280, 'height': 720}, device_scale_factor=3)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url.rstrip('/') + '/monitor')
        page.wait_for_function('clientConfigReady && audioProbePromise === null')
        page.wait_for_function('browserSocketConnected')
        assert page.evaluate('soundEnabled'), 'Native silent WAV probe must work in kiosk autoplay mode'
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
        assert page.evaluate('devicePixelRatio') == 3
        if args.output:
            output = Path(args.output)
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / 'monitor-4k-300-percent.png'))
        context.close()
        kiosk.close()
        print('Native kiosk autoplay and 4K / 300% viewport: OK')
    assert not errors, errors
    print('Browser regression checks passed; no real Telegram messages or acknowledgements sent.')


if __name__ == '__main__':
    main()
