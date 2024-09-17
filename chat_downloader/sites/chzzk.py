import orjson

from .common import (
    Chat,
    BaseChatDownloader,
)

from ..errors import (
    UserNotFound,
    SiteError
)

from ..utils.core import (
    attempts
)

from ..debugging import (
    log
)

import websocket
import queue
import rel

from requests.exceptions import RequestException
from json.decoder import JSONDecodeError
from threading import Event

# NOTE: https://github.com/kimcore/chzzk/blob/main/src/chat/chat.ts

class ChatCommands:
    # Define command codes as class attributes
    PING = 0
    PONG = 10000
    CONNECT = 100
    CONNECTED = 10100
    SEND_CHAT = 3101
    REQUEST_RECENT_CHAT = 5101
    RESPONSE_RECENT_CHAT = 15101
    EVENT = 93006
    CHAT = 93101
    DONATION = 93102
    KICK = 94005
    BLOCK = 94006
    BLIND = 94008
    NOTICE = 94010
    PENALTY = 94015


class ChzzkChatDownloader(BaseChatDownloader):
    _NAME = 'chzzk.naver.com'

    _SITE_DEFAULT_PARAMS = {
        'format': 'chzzk',
    }

    _VALID_URLS = {
        # e.g. 'https://chzzk.naver.com/live/channel_id'
        '_get_chat_by_channel_id': r"https?://chzzk\.naver\.com/live/(?P<channel_id>[^/?]+)",

        # e.g. 'https://chzzk.naver.com/video/video_id'
        '_get_chat_by_video_id': r"https?://chzzk\.naver\.com/video/(?P<video_id>[^/?]+)",
    }

    _LIVE_DETAIL_URL = "https://api.chzzk.naver.com/service/v2/channels/{channel_id}/live-detail"
    _ACCESS_TOKEN_URL = "https://comm-api.game.naver.com/nng_main/v1/chats/access-token?channelId={chat_channel_id}&chatType=STREAMING"

    _DEFAULT_NID_AUT = "nVrU5HBws13iBYAnAa5D7bnZrUtp69cn6T+V7BHQIXhHrBexYt9yDBjPS2+YvWdb"
    _DEFAULT_NID_SES = "AAABoWkzOGZj+RIiu6C4Jakp+RdUsaMtRgLbMzO8kh5it7a34ADYVPTvZKtrw9hPNd88WgRjMbyB8+dYw00N+jJckHHo6Q9szDa7Gssw1B7jJF0KiwAi6REeaJa3sdQomN/mdrWEHqvlizYg8cKWaIgCc+evNveEoxcd8zwuRPlSorGWcg09gMPmGwhdFN+eT37sWkCY+gU3W0bbOMUsghZQ/ULUif5+Ghv2fq1gfEukHkbbdiEyRqKuhjjiFn1JNj2cb6Mc+cYBOsZOPFqJ5YuYUVYPKLxg5/jVaH++EmUWgEKonVIlL2f0mjEoIoXYEhwMT4b+iu/xo41IWA35am2RkTLu7rVwSIebVTGLL2W5DAapfUje02SZ+jyl6ynEuhHlHf5994/8IJFerfE2Nh9AhWbECzCRpSTDYaolysKQ/uvUtXxmcuUWCtrUAPZQuXWwE0jtpBUzqZjDFuTMG16EetA0b1K3RrlD2BXut1LlTyfXEyy6UgeoijDnR18X6WvamMT3LieM6Q+QOFI3lhrmYnqUEP+UoYpArIHDtAesgQuKwiai6q1ooIsvtVuAIp6Xdw=="

    _USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

    _STATUS_OPEN = "OPEN"

    queue = queue.Queue()
    event = Event()
    cid = None

    def on_message(self, ws, message):
        raw_msg = orjson.loads(message)
        print(raw_msg)
        cmd = raw_msg.get('cmd')
        if cmd == ChatCommands.CONNECTED:
            print('connected')
            self.sid = raw_msg['bdy']['sid']
            self.send_json({
                "ver": "3",
                "svcid": "game",
                "cid": self.cid,
                "cmd": ChatCommands.REQUEST_RECENT_CHAT,
                "tid": 2,
                "sid": self.sid,
                "bdy": {
                    'recentMessageCount': 50
                }
            })
            return
        elif cmd == ChatCommands.PING:
            self.send_json({'ver': '3', 'cmd': ChatCommands.PONG})
            return
        elif cmd == ChatCommands.PONG:
            return

        if 'bdy' not in raw_msg:
            return

        raw_body = raw_msg['bdy']
        if isinstance(raw_body, list):
            chat_msgs = raw_body
        elif isinstance(raw_body, dict):
            chat_msgs = raw_body.get('messageList', [raw_body])
        else:
            log('error', f'Unknown format: {raw_body}')
            return

        for chat_msg in chat_msgs:
            if 'msgTime' not in chat_msg and 'messageTime' not in chat_msg:
                continue

            msgTime = 'msgTime' if 'msgTime' in chat_msg else 'messageTime'
            msg = 'msg' if 'msg' in chat_msg else 'content'
            msgTypeCode = 'msgTypeCode' if 'msgTypeCode' in chat_msg else 'messageTypeCode'
            userId = 'uid' if 'uid' in chat_msg else 'userId'

            # System messages
            if chat_msg.get(msgTypeCode) in (30, 121,):
                continue
            if 'profile' not in chat_msg and 'extras' not in chat_msg:
                continue

            chat_msg['profile'] = orjson.loads(chat_msg['profile']) if chat_msg.get('profile') else {}
            display_name = chat_msg['profile'].get('nickname', '')

            chat_msg['extras'] = orjson.loads(chat_msg['extras']) if chat_msg.get('extras') else {}
            emotes = chat_msg['extras'].get('emojis')
            pay_amount = chat_msg['extras'].get('payAmount')

            data = {}
            data['timestamp'] = chat_msg[msgTime] * 1000
            data['message_id'] = f'{self.cid}{chat_msg["userId"]}{chat_msg[msgTime]}'
            data['message'] = chat_msg[msg]
            data['message_type'] = str(chat_msg[msgTypeCode])
            data['author'] = {
                'display_name': display_name,
                'id': chat_msg[userId],
            }
            if emotes:
                data['emotes'] = emotes
            if pay_amount:
                data['pay_amount'] = pay_amount
            self.queue.put(data)

    def on_error(self, ws, error):
        print(error)

    def on_close(self, ws, close_status_code, close_msg):
        # probably only when server requested to close
        print("### closed ###")

    def on_open(self, ws):
        print("Opened connection")
        self.send_json({
            "ver": "3",
            "svcid": "game",
            "cid": self.cid,
            "cmd": ChatCommands.CONNECT,
            "tid": 1,
            "bdy": {
                "uid": None,
                "devType": 2001,
                "accTkn": self.accTkn,
                "auth": "READ"
            }
        })

    def send_json(self, data):
        print(f'send: {data}')
        self.websocket.send(orjson.dumps(data))

    def _get_chat_by_video_id(self, match, params):
        # TODO: vod support
        return

    def abort(self):
        rel.abort()
        self.event.set()

    def _get_chat_messages_by_channel_id(self, chat_channel_id, chat_access_token, params):
        self.cid = chat_channel_id
        self.server_id = sum([ord(c) for c in chat_channel_id]) % 9 + 1
        self.accTkn = chat_access_token
        self.websocket = websocket.WebSocketApp(
            url=f'wss://kr-ss{self.server_id}.chat.naver.com/chat',
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_reconnect=self.on_open,
        )
        self.timeout = params.get('message_receive_timeout')
        self.websocket.run_forever(
            ping_interval=20,
            ping_payload=orjson.dumps({}),
            dispatcher=rel,
        )
        rel.signal(2, lambda: self.abort())
        rel.dispatch()
        message_count = 0
        try:
            while not self.event.is_set():
                try:
                    data = self.queue.get(timeout=self.timeout)
                except queue.Empty:
                    yield {}
                    continue

                message_count += 1
                yield data
                log('debug', f'Total number of messages: {message_count}')
        except Exception as e:
            log('error', e)
        finally:
            self.websocket.close()

    def _get_chat_by_channel_id(self, match, params):
        return self.get_chat_by_channel_id(match.group('channel_id'), params)

    def get_chat_by_channel_id(self, channel_id, params):
        print(f'params: {params}')
        cookies = {"NID_AUT": params.get('NID_AUT', self._DEFAULT_NID_AUT),
                   "NID_SES": params.get('NID_SES', self._DEFAULT_NID_SES)}
        max_attempts = params.get('max_attempts')
        for attempt_number in attempts(max_attempts):
            try:
                live_info = self._session_get_json(self._LIVE_DETAIL_URL.format(channel_id=channel_id))['content']
                break
            except (JSONDecodeError, RequestException) as e:
                self.retry(attempt_number, error=e, **params)

        if not live_info:
            raise UserNotFound(f'Unable to find Chzzk channel: "{channel_id}"')

        is_live = live_info['status'] == self._STATUS_OPEN
        title = live_info['liveTitle']
        live_id = live_info['liveId']

        chat_channel_id = live_info['chatChannelId']

        for attempt_number in attempts(max_attempts):
            try:
                access_token_info = self._session_get_json(self._ACCESS_TOKEN_URL.format(chat_channel_id=chat_channel_id),
                                                           cookies=cookies)['content']
                break
            except (JSONDecodeError, RequestException) as e:
                self.retry(attempt_number, error=e, **params)

        if not access_token_info:
            raise SiteError(f'Unable to get access token of Chzzk: "{channel_id}"')

        chat_access_token = access_token_info['accessToken']
        params['chat_extra_token'] = access_token_info['extraToken']
        params['channel_id'] = channel_id
        return Chat(
            self._get_chat_messages_by_channel_id(chat_channel_id, chat_access_token, params),
            title=title,
            duration=None,
            status='live' if is_live else 'upcoming',  # Always live or upcoming
            video_type='video',
            id=live_id
        )
