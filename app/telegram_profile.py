class TelegramProfile:
    SHOW = '👤 Мои данные'
    EDIT = '✏️ Изменить данные'
    FILL = '📝 Заполнить профиль'
    DELETE = '🗑 Удалить мои данные'
    CANCEL = '❌ Отмена'

    def __init__(self, service):
        self.service = service

    def keyboard(self, profile, token=None):
        rows = [[{'text':self.SHOW}]]
        if profile and not profile['blocked'] and not profile['profile_locked']:
            rows.append([{'text':self.EDIT if profile['profile_complete'] else self.FILL}])
            if profile['profile_complete']:
                rows.append([{'text':self.DELETE}])
        subscription = self.service.private.menu(token,profile) if token else None
        if subscription:
            rows.append([{'text':subscription}])
        rows.append([{'text':self.CANCEL}])
        return {'keyboard':rows,'resize_keyboard':True,'is_persistent':True}

    async def send(self, token, user, text):
        profile = self.service.employees.by_user(user['id'])
        return await self.service.api.call(token,'sendMessage',chat_id=user['id'],text=text,
                                           reply_markup=self.keyboard(profile,token))

    def cancel(self, setting, user):
        for kind in ('profile','profile_delete'):
            flow = self.service.flows.pending(setting['bot_id'],user['id'],kind)
            if flow:
                self.service.flows.update(flow['id'],done=True)

    async def show(self, token, user, prefix=''):
        profile = self.service.employees.by_user(user['id'])
        if not profile:
            profile = self.service.employees.observe(user)
        text = ('👤 Мои данные\nФИО: ' + profile['full_name'] + '\nЛогин: '
                + (profile['login'] or 'не заполнен') + '\nTelegram ID: ' + str(user['id']))
        if profile['blocked']:
            text += '\n⛔ Профиль заблокирован. Обратитесь к администратору.'
        elif profile['profile_locked']:
            text += '\n✅ Профиль подтверждён. Данные закреплены администратором. Самостоятельное изменение и удаление недоступны.'
        elif not profile['profile_complete']:
            text += '\nНажмите «Заполнить профиль», чтобы указать логин и ФИО.'
        if not profile['profile_locked'] and not profile['blocked']:
            text += '\nДля подписки на уведомления требуется подтверждение администратора.'
        return await self.send(token,user,(prefix+'\n\n' if prefix else '')+text)

    def editable(self, user):
        profile = self.service.employees.by_user(user['id'])
        if profile and (profile['blocked'] or profile['profile_locked']):
            raise ValueError('Изменение и удаление данных запрещено администратором')
        return profile

    async def start(self, setting, token, user):
        self.editable(user)
        self.cancel(setting,user)
        flow = self.service.flows.create(setting,user['id'],'profile',user['id'],step='login')
        sent = await self.send(token,user,'Введите корпоративный логин, например i.petrov. Для выхода нажмите «Отмена».')
        self.service.flows.update(flow['id'],prompt_id=sent['message_id'])

    async def confirm(self, setting, token, user, flow):
        service = self.service
        if flow['kind'] == 'profile_delete':
            if flow['step'] != 'confirm':
                raise ValueError('Запрос устарел')
            service.employees.clear_self(str(user['id']),flow['data']['employee_id'],flow['data']['updated_at'])
            service.flows.update(flow['id'],done=True)
            await service.interactions.finish(token,flow,'Корпоративные данные удалены. История вызовов сохранена.')
            await self.show(token,user,'Корпоративный профиль очищен.')
        elif flow['kind'] == 'profile' and flow['step'] == 'confirm':
            try:
                service.employees.save(str(user['id']),flow['data']['login'],flow['data']['full_name'])
            except ValueError as exc:
                service.flows.update(flow['id'],done=True)
                await service.interactions.finish(token,flow,str(exc))
                profile = service.employees.by_user(user['id'])
                if profile and not profile['blocked'] and not profile['profile_locked']:
                    await self.start(setting,token,user)
                else:
                    await self.show(token,user)
                return str(exc)
            service.flows.update(flow['id'],done=True)
            await service.interactions.finish(token,flow,'✅ Профиль сохранён')
            await self.show(token,user,'✅ Данные сохранены')
        else:
            raise ValueError('Запрос устарел')
        for item in service.calls.refresh_profile([str(user['id'])]):
            await service.interactions.changed(item,'profile')
        return 'Сохранено'

    async def message(self, setting, token, user, text):
        command = text.split(maxsplit=1)[0].lower() if text else ''
        try:
            if command in {'/start','/menu','/profile'} or text == self.SHOW:
                self.cancel(setting,user)
                await self.show(token,user)
            elif command in {'/cancel','/cancel@'+setting['bot_username'].lower()} or text == self.CANCEL:
                self.cancel(setting,user)
                await self.show(token,user,'Ввод отменён')
            elif command in {'/sync','/sync@'+setting['bot_username'].lower()} or text in {self.EDIT,self.FILL}:
                await self.start(setting,token,user)
            elif text == self.DELETE:
                profile = self.editable(user)
                if not profile or not profile['profile_complete']:
                    await self.show(token,user,'Корпоративные данные ещё не заполнены')
                    return True
                self.cancel(setting,user)
                flow = self.service.flows.create(setting,user['id'],'profile_delete',user['id'],step='confirm',
                    data={'employee_id':profile['id'],'updated_at':profile['updated_at']})
                sent = await self.service.api.call(token,'sendMessage',chat_id=user['id'],
                    text='Удалить ваш корпоративный логин и ФИО? Telegram-контакт, история вызовов и ограничения администратора сохранятся.',
                    reply_markup={'inline_keyboard':[[{'text':'🗑 Удалить','callback_data':'pdelete:'+flow['id']},
                                                      {'text':'Отмена','callback_data':'abort:'+flow['id']}]]})
                self.service.flows.update(flow['id'],prompt_id=sent['message_id'])
            else:
                flow = self.service.flows.pending(setting['bot_id'],user['id'],'profile')
                if not flow:
                    await self.show(token,user)
                    return True
                self.editable(user)
                if flow['step'] == 'login':
                    login,_ = self.service.employees.validate(text,'Тест Пользователь')
                    self.service.flows.update(flow['id'],step='name',data={'login':login})
                    await self.send(token,user,'Введите ФИО: имя и фамилию, при необходимости отчество.')
                elif flow['step'] == 'name':
                    login,name = self.service.employees.validate(flow['data']['login'],text)
                    sent = await self.service.api.call(token,'sendMessage',chat_id=user['id'],
                        text=f'Сохранить данные?\n{name}\n{login}',
                        reply_markup={'inline_keyboard':[[{'text':'✅ Сохранить','callback_data':'profile:'+flow['id']},
                                                          {'text':'Отмена','callback_data':'abort:'+flow['id']}]]})
                    self.service.flows.update(flow['id'],step='confirm',data={'login':login,'full_name':name},prompt_id=sent['message_id'])
                else:
                    await self.send(token,user,'Нажмите «Сохранить» под подтверждением или «Отмена», чтобы начать заново.')
        except ValueError as exc:
            await self.send(token,user,str(exc))
        return True
