"""Public icon navigation, sound state and shared support form; settings writes are mocked."""
import argparse
import base64
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://localhost:8000')
    parser.add_argument('--browser')
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True,exist_ok=True)
    support_url = 'https://support.example.test/help'
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,executable_path=args.browser)
        context = browser.new_context()
        page = context.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        def config(route):
            response = route.fetch()
            route.fulfill(response=response,json={**response.json(),'support_url':support_url})
        page.route('**/api/areas',config)
        page.route('**/api/client-config',config)
        for path in ['/','/monitor','/monitor?area_id=spb']:
            page.goto(args.url+path)
            page.locator('#supportLink').wait_for()
            assert page.locator('#supportLink').get_attribute('href')==support_url
            assert page.locator('#supportLink').get_attribute('rel')=='noopener noreferrer'
            assert page.get_by_role('link',name='Скачать приложение',exact=True).get_attribute('href')=='/download-app'
            for width,height in [(1920,1080),(1280,900),(960,1080),(768,1024),(480,800),(320,640)]:
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(path,width)
                rects = page.locator('.nav-actions .nav-icon-button:visible').evaluate_all('''buttons=>buttons.map(button=>{
                    const rect=button.getBoundingClientRect();return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:rect.width,height:rect.height};})''')
                for rect in rects:
                    assert rect['left']>=0 and rect['right']<=width+1,(path,width,rect)
                    assert abs(rect['width']-rect['height'])<1 and 44<=rect['width']<=64,(path,width,rect)
                for index,rect in enumerate(rects):
                    for other in rects[index+1:]:
                        assert rect['right']<=other['left']+1 or other['right']<=rect['left']+1 or rect['bottom']<=other['top']+1 or other['bottom']<=rect['top']+1
                if width in (1280,480): page.screenshot(path=str(output/f'{"home" if path=="/" else "monitor"}-{width}.png'),full_page=True)
            if path!='/':
                page.evaluate('setSoundEnabled(true);applyVolume(75,false)')
                assert page.locator('#enableSoundButton use').get_attribute('href').endswith('#sound-on')
                page.evaluate('applyVolume(0,false)')
                assert page.locator('#enableSoundButton use').get_attribute('href').endswith('#sound-off')
                assert 'blocked' in page.locator('#enableSoundButton').get_attribute('class')
                page.locator('#enableSoundButton').click()
                assert page.locator('#volumePanel').is_visible()
        page.goto(args.url+'/admin/login')
        assert page.get_by_role('link',name='Главная',exact=True).get_attribute('href')=='/'
        page.locator('#supportLink').wait_for()
        assert page.locator('#supportLink').get_attribute('href')==support_url
        for width in (1280,480,320):
            page.set_viewport_size({'width':width,'height':800})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert page.locator('#supportLink').bounding_box()['width']==44
        page.route('**/api/client-config',lambda route:route.fulfill(json={'support_url':''}))
        with page.expect_response('**/api/client-config'):
            page.reload()
        assert page.locator('#supportLink').is_hidden()
        settings = dotenv_values(Path(__file__).resolve().parents[1]/'secret.env')
        authorization = base64.b64encode(f"{settings['ADMIN_USERNAME']}:{settings['ADMIN_PASSWORD']}".encode()).decode()
        admin = browser.new_context(extra_http_headers={'Authorization':'Basic '+authorization})
        editor = admin.new_page()
        editor.on('pageerror',lambda error:errors.append(str(error)))
        saved = []
        def admin_settings(route):
            if route.request.method=='PUT': saved.append(route.request.post_data_json['support_url'])
            route.fulfill(json={'support_url':saved[-1] if saved else ''})
        editor.route('**/api/admin/settings',admin_settings)
        editor.route('**/api/client-config',config)
        editor.goto(args.url+'/admin')
        editor.locator('#supportLink').wait_for()
        assert editor.locator('#supportLink').get_attribute('href')==support_url
        editor.get_by_role('button',name='Общие настройки',exact=True).click()
        editor.locator('#supportUrlInput').fill(support_url)
        editor.locator('#globalSettingsForm button').click()
        editor.get_by_text('Сохранено для всех локаций',exact=True).wait_for()
        assert saved==[support_url]
        editor.locator('#supportUrlInput').fill('')
        editor.locator('#globalSettingsForm button').click()
        editor.wait_for_function("document.querySelector('#supportLink').hidden")
        assert saved==[support_url,'']
        for width in (1280,960,480,320):
            editor.set_viewport_size({'width':width,'height':900})
            assert editor.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        browser.close()
    assert not errors,errors
    print('Shared support form, download/home links, icon geometry, 0% sound state and 320-1920px layouts: OK')


if __name__=='__main__':
    main()
