"""Employee admin UI checks with mocked writes; requires Playwright."""

import argparse
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--browser', default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    settings = dotenv_values(Path(__file__).resolve().parents[1] / 'secret.env')
    username = os.getenv('ADMIN_USERNAME') or settings.get('ADMIN_USERNAME')
    password = os.getenv('ADMIN_PASSWORD') or settings.get('ADMIN_PASSWORD')
    if not username or not password:
        raise SystemExit('Admin credentials required in environment or local secret.env')
    employees = [{'id':'auto-91','telegram_user_id':'91','login':'','full_name':'Иван','display_name':'Иван',
        'profile_complete':False,'blocked':False,'allowed_areas':None,'telegram_name':'Иван','telegram_username':'test_employee',
        'last_seen_at_display':'20.10.2026 17:00:00'}]
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        context = browser.new_context(http_credentials={'username':username,'password':password})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def handle(route):
            request = route.request
            path = urlsplit(request.url)
            if request.method == 'POST':
                data = request.post_data_json
                if any(item['login'] == data['login'].lower() for item in employees):
                    route.fulfill(status=400, json={'detail':'Логин уже существует'})
                    return
                item = {'id':'manual-'+str(len(employees)),'blocked':False,'allowed_areas':None,
                        **data,'login':data['login'].lower(),'profile_complete':True}
                item['display_name'] = item['full_name']+' · '+item['login']
                employees.append(item)
                route.fulfill(json={'item':item})
            elif request.method == 'PUT':
                item = next(item for item in employees if item['id'] == path.path.rsplit('/',1)[1])
                item.update(request.post_data_json)
                item['profile_complete'] = bool(item['login'])
                item['display_name'] = item['full_name']+(' · '+item['login'] if item['login'] else '')
                route.fulfill(json={'item':item})
            elif path.path.endswith('/audit'):
                route.fulfill(json={'items':[]})
            else:
                query = parse_qs(path.query).get('q',[''])[0].casefold().lstrip('@')
                items = [item for item in employees if query in ' '.join(str(value or '') for value in item.values()).casefold()]
                route.fulfill(json={'items':items,'page':1,'total':len(items),'total_pages':1})

        page.route('**/api/admin/employees**', handle)
        page.goto(args.url.rstrip('/') + '/admin')
        page.get_by_role('button', name='Сотрудники', exact=True).click()
        page.locator('.employee-list article').first.wait_for()
        card = page.locator('.employee-list article').first
        assert card.locator('.employee-badge').is_visible()
        card.get_by_label('Корпоративный логин', exact=True).fill('i.petrov')
        card.get_by_label('ФИО', exact=True).fill('Иван Петров')
        card.get_by_label('Запретить изменение и удаление данных в боте', exact=True).check()
        card.get_by_role('button', name='Сохранить', exact=True).click()
        card.get_by_text('Сохранено', exact=True).wait_for()
        assert not card.locator('.employee-badge').is_visible()
        assert employees[0]['profile_locked'] is True

        page.locator('.employee-create summary').click()
        form = page.locator('.employee-create-form')
        form.get_by_label('Корпоративный логин', exact=True).fill('a.ivanova')
        form.get_by_label('ФИО', exact=True).fill('Анна Иванова')
        form.get_by_role('button', name='Создать сотрудника', exact=True).click()
        page.get_by_text('Создан: Анна Иванова · a.ivanova', exact=True).wait_for()
        assert employees[-1]['telegram_user_id'] is None
        assert employees[-1]['profile_complete']
        form.get_by_label('Корпоративный логин', exact=True).fill('a.ivanova')
        form.get_by_label('ФИО', exact=True).fill('Анна Иванова')
        form.get_by_role('button', name='Создать сотрудника', exact=True).click()
        page.get_by_text('Логин уже существует', exact=True).wait_for()
        assert len(employees) == 2

        page.get_by_role('searchbox', name='Поиск сотрудника').fill('')
        page.wait_for_function('document.querySelectorAll(".employee-list article").length === 2')
        employees.append({'id':'auto-92','telegram_user_id':'92','login':'','full_name':'Новый участник','display_name':'Новый участник',
                          'profile_complete':False,'allowed_areas':None,'blocked':False})
        page.get_by_role('heading', name='Новый участник', exact=True).wait_for(timeout=15000)
        pending = page.locator('.employee-list article').filter(has=page.get_by_role('heading', name='Новый участник', exact=True))
        pending.get_by_label('Корпоративный логин', exact=True).fill('unsaved.login')
        page.wait_for_timeout(10500)
        assert pending.get_by_label('Корпоративный логин', exact=True).input_value() == 'unsaved.login'
        for width in (1440,768,390):
            page.set_viewport_size({'width':width,'height':950})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
            if args.output:
                output = Path(args.output)
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'employee-admin-{width}.png'), full_page=True)
        assert not errors, errors
        print('Employee UI: create, duplicate feedback, pending profile editing, auto-refresh, unsaved input preservation and responsive layout OK')
        browser.close()


if __name__ == '__main__':
    main()
