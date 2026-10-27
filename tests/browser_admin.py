"""Admin roles, location workspace and responsive layout; all writes are mocked."""
import argparse
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--browser')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    settings = dotenv_values(Path(__file__).resolve().parents[1]/'secret.env')
    areas = [{'area_id':key,'display_name':name,'ha_name':key,'slug':key,'enabled':True,
              'color':'#60a5fa','button_count':55,'active_button_count':54,'sound_repeat_mode':'seconds',
              'sound_repeat_seconds':25,'sound_repeat_count':None,'sound_path':None,'history_sync_hours':24,
              'ack_button_text':'Убрать'} for key,name in [('spb','Санкт-Петербург'),('msk','Москва')]]
    errors = []
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        for role in ('owner','admin','location'):
            requests = []
            visible = areas[:1] if role == 'location' else areas
            authorization = base64.b64encode(f"{settings['ADMIN_USERNAME']}:{settings['ADMIN_PASSWORD']}".encode()).decode()
            context = browser.new_context(extra_http_headers={'Authorization':'Basic '+authorization})
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            def handle(route):
                request = route.request
                parsed = urlsplit(request.url)
                path = parsed.path
                requests.append((request.method,path,request.post_data))
                if path.endswith('/me'):
                    payload = {'user':{'username':'test-admin','role':role,'allowed_areas':['spb'] if role=='location' else None}}
                elif path.endswith('/areas'):
                    payload = {'items':visible} if request.method == 'GET' else {'item':request.post_data_json}
                elif path.endswith('/sounds'):
                    names = ['test.mp3'] + ['Очень_длинное_название_звука_'*3+str(index)+'.mp3' for index in range(6)]
                    payload = {'items':[{'name':name,'path':'sound/'+name,'url':'/sound/'+name,'size':1234} for name in names]}
                elif '/sounds/' in path:
                    route.fulfill(status=409,json={'detail':'Звук с таким именем уже существует'})
                    return
                elif path.endswith('/telegram'):
                    payload = {'items':[{'area_id':area['area_id'],'display_name':area['display_name'],
                        'enabled':False,'token_configured':False,'setup_until':0,'chat_id':None,'setups':[],
                        'delivery_stats':{}} for area in visible], 'key_ready':True,'enabled':False,'event_ttl_seconds':300}
                elif '/employees' in path:
                    if path.endswith('/detail'):
                        payload = {'item':{'id':'test-employee','display_name':'Иван Петров · i.petrov','full_name':'Иван Петров',
                            'login':'i.petrov','blocked':False,'allowed_areas':None,'area_enabled':True},'locations':[]}
                    elif path.endswith('/activity'):
                        payload = {'items':[],'total':0,'page':1,'total_pages':1}
                    elif path.endswith('/profile') and request.method == 'PUT':
                        data = request.post_data_json
                        payload = {'item':{**data,'telegram_user_id':'91','display_name':data['full_name']+' · '+data['login'],'profile_complete':True}}
                    elif request.method == 'GET':
                        payload = {'items':[{'id':'test-employee','display_name':'Иван Петров · i.petrov','full_name':'Иван Петров',
                            'login':'i.petrov','blocked':False,'allowed_areas':None,'area_enabled':True}],
                            'page':1,'total':1,'total_pages':1}
                    else:
                        payload = {'saved':True}
                elif path.endswith('/users'):
                    payload = {'items':[{'username':'regional_'+'long_login_'*8,'role':'location','is_active':True,'allowed_areas':['spb']}] +
                        ([{'username':'main-admin','role':'owner','is_active':True,'allowed_areas':None},
                          {'username':'general-admin','role':'admin','is_active':False,'allowed_areas':None}] if role=='owner' else [])}
                elif path.endswith('/logs'):
                    payload = {'items':[],'actions':[]}
                elif path.endswith('/audit'):
                    payload = {'items':[],'total':0,'page':1,'total_pages':1,'actors':[]}
                else:
                    payload = {'items':[]}
                route.fulfill(json=payload)
            page.route('**/api/admin/**', handle)
            page.goto(args.url+'/admin')
            page.locator('#adminLocationSelect option').first.wait_for(state='attached')
            page.wait_for_function('document.querySelectorAll(".area-settings-section").length === 3')
            assert not any(path.endswith('/telegram') or '/employees' in path or path.endswith('/logs') or path.endswith('/users') for _,path,_ in requests)
            assert page.locator('#adminLocationSelect option').count() == len(visible)
            assert page.locator('.admin-tab-panel.active').count() == 1
            assert page.get_by_role('button',name='Администраторы',exact=True).is_visible() == (role != 'location')
            assert page.get_by_label('Название локации',exact=True).is_disabled() == (role == 'location')
            assert page.get_by_label('Звук уведомления',exact=True).input_value() == ''
            page.get_by_label('Кнопка подтверждения',exact=True).fill('Принято')
            page.get_by_role('button',name='Сохранить',exact=True).click()
            page.get_by_text('Сохранено:',exact=False).wait_for()
            saved = next(json.loads(body) for method,path,body in requests if method=='POST' and path.endswith('/areas'))
            assert saved['area_id']=='spb' and saved['ack_button_text']=='Принято'
            page.get_by_role('button',name='Telegram',exact=True).click()
            page.locator('#telegramRegionList article').wait_for()
            assert page.locator('#telegramRegionList article').count()==1
            if role != 'location':
                page.locator('#adminLocationSelect').select_option('msk')
                page.locator('#telegramRegionList h3').filter(has_text='Москва').wait_for()
            page.get_by_role('button',name='Сотрудники',exact=True).click()
            page.locator('.employee-list article').wait_for()
            assert page.locator('.employee-list input').count()==0
            page.locator('.employee-row').first.click()
            page.locator('.employee-list .telegram-region-card').wait_for()
            checkbox = page.get_by_label('Разрешить действия в боте этой локации',exact=True)
            checkbox.uncheck()
            page.locator('.employee-list').get_by_text('Сохранено',exact=True).wait_for()
            assert any(method=='PUT' and '/areas/' in path for method,path,_ in requests)
            if role=='location':
                assert not page.locator('#allEmployeeProfiles').is_visible()
                assert not page.locator('.employee-create').is_visible()
                employee = page.locator('.employee-list article').first
                assert employee.get_by_label('Корпоративный логин',exact=True).is_editable()
                assert employee.get_by_label('ФИО',exact=True).is_editable()
                assert not employee.get_by_label('Telegram ID',exact=True).is_editable()
                assert not employee.get_by_label('Профиль заблокирован',exact=True).is_visible()
                assert not employee.get_by_label('Доступ ко всем локациям',exact=True).is_visible()
                employee.get_by_label('Корпоративный логин',exact=True).fill('updated.employee')
                employee.get_by_label('ФИО',exact=True).fill('Updated Employee')
                employee.get_by_label('Запретить изменение и удаление данных в боте',exact=True).check()
                page.locator('.employee-list').get_by_role('button',name='Сохранить',exact=True).click()
                page.locator('.employee-list .telegram-feedback').last.filter(has_text='Сохранено').wait_for()
                saved = [json.loads(body) for method,path,body in requests if method=='PUT' and path.endswith('/profile')][-1]
                assert saved=={'area_id':'spb','login':'updated.employee','full_name':'Updated Employee','profile_locked':True}
            else:
                page.get_by_role('button',name='Администраторы',exact=True).click()
                page.locator('#adminUserList .admin-user-card').first.wait_for()
                page.screenshot(path=str(output/f'{role}-users-before.png'),full_page=True)
                assert page.locator('#newAdminRole option[value="admin"]').evaluate('(option) => option.hidden') == (role != 'owner')
                assert page.locator('.admin-user-card .access-areas input').count() == (4 if role=='owner' else 2)
            page.get_by_role('button',name='Библиотека звуков',exact=True).click()
            if role == 'location':
                assert page.get_by_role('button',name='Удалить',exact=True).count()==0
            else:
                assert page.locator('.sound-library-item').get_by_role('button',name='Удалить',exact=True).first.is_visible()
            assert page.locator('.sound-library-item').count()==6
            page.locator('#soundNextButton').click()
            assert page.locator('.sound-library-item').count()==1
            page.locator('#soundSearchInput').fill('test.mp3')
            assert page.locator('.sound-library-item').count()==1
            page.locator('#soundSearchInput').fill('')
            for width,height in [(1440,900),(960,1080),(768,1024),(480,800),(320,640)]:
                page.set_viewport_size({'width':width,'height':height})
                for tab in ['areas','telegram','employees','sounds','profile','logs'] + ([] if role=='location' else ['users']):
                    page.locator(f'[data-tab="{tab}"]').click()
                    if width in (1440,480):
                        page.screenshot(path=str(output/f'{role}-{tab}-{width}.png'),full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (role,tab,width,'overflow')
                    assert page.locator('.admin-tab-panel.active').count()==1
                    assert page.locator('.admin-tab-panel.active').evaluate('(panel) => panel.scrollWidth <= panel.clientWidth + 1'), (role,tab,width,'panel overflow')
                    results.append(f'{role} {tab} {width}x{height}: OK')
            context.close()
        browser.close()
    assert not errors, errors
    print('\n'.join(results))


if __name__ == '__main__':
    main()
