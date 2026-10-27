"""Real session login/logout and mocked audit rendering; never changes app settings."""
import argparse
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://localhost:8000')
    parser.add_argument('--browser')
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True,exist_ok=True)
    settings = dotenv_values(Path(__file__).resolve().parents[1]/'secret.env')
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,executable_path=args.browser)
        context = browser.new_context()
        page = context.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url+'/admin')
        page.wait_for_url('**/admin/login')
        page.get_by_label('Логин',exact=True).fill(settings['ADMIN_USERNAME'])
        page.get_by_label('Пароль',exact=True).fill('deliberately-wrong-password')
        page.get_by_role('button',name='Войти',exact=True).click()
        page.get_by_text('Неверный логин или пароль',exact=True).wait_for()
        page.get_by_label('Пароль',exact=True).fill(settings['ADMIN_PASSWORD'])
        page.get_by_role('button',name='Войти',exact=True).click()
        page.wait_for_url(args.url+'/admin')
        page.locator('#adminLocationSelect option').first.wait_for(state='attached')
        cookie = next(cookie for cookie in context.cookies() if cookie['name']=='perehomelab_admin')
        assert cookie['httpOnly'] and cookie['sameSite']=='Strict'
        assert 'perehomelab_admin' not in page.evaluate('document.cookie')
        token = page.evaluate('adminCsrfToken')
        assert token
        denied = context.request.post(args.url+'/api/admin/auth/logout')
        assert denied.status==403
        loaded = page.evaluate('adminAreas')
        area_id = loaded[0]['area_id']
        queries = []
        def audit(route):
            query = parse_qs(urlsplit(route.request.url).query)
            queries.append(query)
            entries = [{'id':1,'username':'test-editor','created_at':'2026-10-26T12:34:56',
                'summary':'Настройки локации сохранены','target':'<img src=x onerror=alert(1)>',
                'category':'location','area_ids':[area_id],'details':'',
                'changes':[{'field':'sound_repeat_seconds','label':'Проигрывание, секунд','before':25,'after':40},
                           {'field':'ack_button_text','label':'Кнопка подтверждения','before':'ФА','after':'Убрать'}]}]
            if query.get('q')==['nothing-found']: entries=[]
            route.fulfill(json={'items':entries,'actors':['test-editor'],'page':1,'total':len(entries),'total_pages':1})
        page.route('**/api/admin/audit?*',audit)
        page.get_by_role('button',name='Журнал',exact=True).click()
        page.locator('.audit-entry').wait_for()
        assert page.locator('.audit-entry img').count()==0
        assert page.locator('.audit-before').first.inner_text()=='25'
        assert page.locator('.audit-after').first.inner_text()=='40'
        page.locator('#auditFilters [name="actor"]').select_option('test-editor')
        page.wait_for_function('document.querySelector("#auditStatus").textContent === "Записей: 1"')
        page.locator('#auditFilters [name="q"]').fill('nothing-found')
        page.get_by_text('По выбранным фильтрам записей нет',exact=True).wait_for()
        assert queries[-1].get('actor')==['test-editor']
        page.get_by_role('button',name='Сбросить фильтры',exact=True).click()
        page.locator('.audit-entry').wait_for()
        for width,height in [(1440,900),(960,1080),(768,1024),(480,800),(320,640)]:
            page.set_viewport_size({'width':width,'height':height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'),width
            assert page.locator('.admin-tab-panel.active').evaluate('(panel)=>panel.scrollWidth<=panel.clientWidth+1'),width
            page.screenshot(path=str(output/f'audit-{width}.png'),full_page=True)
        page.get_by_role('button',name='Выйти',exact=True).click()
        page.wait_for_url('**/admin/login')
        assert not any(cookie['name']=='perehomelab_admin' for cookie in context.cookies())
        page.goto(args.url+'/admin'); page.wait_for_url('**/admin/login')
        page.screenshot(path=str(output/'login-320.png'),full_page=True)
        browser.close()
    assert not errors,errors
    print('Session login, incorrect password, HttpOnly cookie, CSRF, logout, audit filters, XSS and 320-1440px layouts: OK')


if __name__=='__main__':
    main()
