from .common import (
    Chat,
    BaseChatDownloader,
)

from ..debugging import (
    log,
)
import asyncio
import json

from afreeca import AfreecaTV, Chat as AfreecaChat, UserCredential
from afreeca.exceptions import NotStreamingError
from datetime import datetime, timezone
from queue import Queue
from threading import Thread
from hashlib import sha256


class AfreecaChatDownloader(BaseChatDownloader):
    _NAME = 'afreecatv.com'

    _SITE_DEFAULT_PARAMS = {
        'format': 'afreeca',
    }

    _VALID_URLS = {
        # e.g. 'https://play.afreecatv.com/username'
        # e.g. 'https://play.afreecatv.com/username/bno/'
        '_get_chat': r"https?://play\.afreecatv\.com/(?P<username>\w+)(?:/(?P<bno>:\d+))?",
    }

    _DEFAULT_ID = 'playsquad'
    _DEFAULT_PW = 'g17JNU]}bI2}n$p'

    queue = Queue()

    async def _chat_callback(self, chat: AfreecaChat):
        data = {
            'message_id': '',
            'timestamp': int(datetime.now(timezone.utc).timestamp() * 1e6),
            'message': chat.message,
            'flag': ','.join(chat.flags),
            'author': {
                'id': chat.sender_id,
                'display_name': chat.nickname,
            }
        }
        if chat.subscription_month:
            data['author']['subscription_month'] = chat.subscription_month
        data['message_id'] = sha256(json.dumps(data, sort_keys=True).encode('utf8')).hexdigest()

        self.queue.put(data)

    def _get_chat_messages(self):
        self.loop.create_task(self.chat_loader.loop())
        Thread(target=self.loop.run_forever, daemon=True).start()
        message_count = 0
        try:
            while True:
                data = self.queue.get()
                message_count += 1
                yield data
                log('debug', f'Total number of messages: {message_count}')
        except Exception as e:
            log('error', e)
        finally:
            log('debug', 'start cleanup!')
            self.chat_loader.remove_callback(self._chat_callback)
            self.loop.stop()

    def _get_empty_generator(self):
        yield {}
        return

    def _get_chat(self, match, params):
        self.loop = asyncio.get_event_loop()
        chat = self.loop.run_until_complete(self.get_chat_by_username(match.group('username'), params))
        return chat

    async def close_all_aiohttp_connections(self):
        if self.chat_loader.credential._session:
            await self.chat_loader.credential._session.close()
        if self.chat_loader.session:
            await self.chat_loader.session.close()
        if self.chat_loader.connection:
            await self.chat_loader.connection.close()
            self.chat_loader.connection = None

    async def get_chat_by_username(self, username, params):
        cred = await UserCredential.login(params.get('afreeca-id', self._DEFAULT_ID), params.get('afreeca-pw', self._DEFAULT_PW))
        afreeca = AfreecaTV(credential=cred)

        self.chat_loader = await afreeca.create_chat(username)
        self.chat_loader.add_callback(event='chat', callback=self._chat_callback)

        try:
            await self.chat_loader.connect()
            bj_info = self.chat_loader.info
            return Chat(
                self._get_chat_messages(),
                title=bj_info.title,
                duration=None,
                status='live',
                video_type='video',
                id=bj_info.bno
            )

        except NotStreamingError:
            if cred._session:
                await cred._session.close()
            return Chat(
                self._get_empty_generator(),
                title='',
                duration=None,
                status='upcoming',
                video_type='video',
                id=''
            )
