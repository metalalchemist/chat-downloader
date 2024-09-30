import signal

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

from requests.exceptions import RequestException
from json.decoder import JSONDecodeError
from threading import Event, Thread


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

    _LIVE_DETAIL_URL = "https://api.chzzk.naver.com/service/v3/channels/{channel_id}/live-detail"
    _ACCESS_TOKEN_URL = "https://comm-api.game.naver.com/nng_main/v1/chats/access-token?channelId={chat_channel_id}&chatType=STREAMING"

    _DEFAULT_NID_AUT = "nVrU5HBws13iBYAnAa5D7bnZrUtp69cn6T+V7BHQIXhHrBexYt9yDBjPS2+YvWdb"
    _DEFAULT_NID_SES = "AAABoWkzOGZj+RIiu6C4Jakp+RdUsaMtRgLbMzO8kh5it7a34ADYVPTvZKtrw9hPNd88WgRjMbyB8+dYw00N+jJckHHo6Q9szDa7Gssw1B7jJF0KiwAi6REeaJa3sdQomN/mdrWEHqvlizYg8cKWaIgCc+evNveEoxcd8zwuRPlSorGWcg09gMPmGwhdFN+eT37sWkCY+gU3W0bbOMUsghZQ/ULUif5+Ghv2fq1gfEukHkbbdiEyRqKuhjjiFn1JNj2cb6Mc+cYBOsZOPFqJ5YuYUVYPKLxg5/jVaH++EmUWgEKonVIlL2f0mjEoIoXYEhwMT4b+iu/xo41IWA35am2RkTLu7rVwSIebVTGLL2W5DAapfUje02SZ+jyl6ynEuhHlHf5994/8IJFerfE2Nh9AhWbECzCRpSTDYaolysKQ/uvUtXxmcuUWCtrUAPZQuXWwE0jtpBUzqZjDFuTMG16EetA0b1K3RrlD2BXut1LlTyfXEyy6UgeoijDnR18X6WvamMT3LieM6Q+QOFI3lhrmYnqUEP+UoYpArIHDtAesgQuKwiai6q1ooIsvtVuAIp6Xdw=="

    _USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"

    queue = queue.Queue()
    event = Event()
    chat_channel_id = None
    live_channel_id = None
    cookies = None
    websocket_thread = None
    timeout = 5
    max_retries = 5
    proxy_host = None
    proxy_port = None

    def on_message(self, ws, message):
        raw_msg = orjson.loads(message)
        cmd = raw_msg.get('cmd')
        if cmd == ChatCommands.CONNECTED:
            print('connected')
            self.sid = raw_msg['bdy']['sid']
            self.send_json({
                "ver": "3",
                "svcid": "game",
                "cid": self.chat_channel_id,
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
            message_time = chat_msg.get('messageTime') or chat_msg.get('msgTime')
            if not message_time:
                continue
            message = chat_msg.get('msg') or chat_msg.get('content')
            message_type = chat_msg.get('msgTypeCode') or chat_msg.get('messageTypeCode')
            user_id = chat_msg.get('uid') or chat_msg.get('userId')

            # System messages
            if message_type in (30, 121,):
                continue
            if 'profile' not in chat_msg and 'extras' not in chat_msg:
                continue

            profile = chat_msg.get('profile', '{}')
            if profile is None:
                display_name = ""
            else:
                display_name = orjson.loads(profile).get('nickname', '')
            extras = orjson.loads(chat_msg.get('extras', '{}'))
            emotes = extras.get('emojis')
            pay_amount = extras.get('payAmount')

            data = {
                'timestamp': message_time * 1000,
                'message_id': f'{self.chat_channel_id}{user_id}{message_time}',
                'message': message,
                'message_type': str(message_type),
                'extras': extras,
                'author': {
                    'display_name': display_name,
                    'id': user_id,
                }
            }
            if emotes:
                data['emotes'] = emotes
            if pay_amount:
                data['pay_amount'] = pay_amount
            self.queue.put(data)

    def on_error(self, ws, error):
        # maybe this can be change of cid, retrieve info once more to confirm
        print("################## WEBSOCKET ERROR ###############################")
        print(error)

    def on_close(self, ws, close_status_code, close_msg):
        # probably only when server requested to close
        print("### closed ###")
        if not self.event.is_set():
            # not closed by request, try again
            is_live, _, _ = self.connect_websocket()
            if not is_live:
                # do not retry if not live
                self.terminate()

    def on_open(self, ws):
        print("Opened connection")
        self.send_json({
            "ver": "3",
            "svcid": "game",
            "cid": self.chat_channel_id,
            "cmd": ChatCommands.CONNECT,
            "tid": 1,
            "bdy": {
                "uid": None,
                "devName": "Google Chrome/129.0.0.0",
                "timezone": "Asia/Seoul",
                "devType": 2001,
                "libVer": "4.9.3",
                "locale": "ko",
                "osVer": "Windows/10",
                "accTkn": self.access_token,
                "auth": "READ"
            }
        })

    def send_json(self, data):
        self.websocket.send(orjson.dumps(data))

    def _get_chat_by_video_id(self, match, params):
        # TODO: vod support
        return

    def connect_websocket(self):
        is_live, live_id, live_title, chat_channel_id = self.get_channel_detail()
        self.chat_channel_id = chat_channel_id
        self.server_id = sum([ord(c) for c in chat_channel_id]) % 9 + 1
        self.access_token = self.get_chat_access_token()
        self.websocket = websocket.WebSocketApp(
            url=f'wss://kr-ss{self.server_id}.chat.naver.com/chat',
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_reconnect=self.on_open,
        )
        if self.websocket_thread:
            self.websocket_thread.join(timeout=self.timeout)
        self.websocket_thread = Thread(
            target=self.websocket.run_forever,
            kwargs=dict(
                ping_interval=20,
                # TODO: ping opcode matters?
                ping_payload=orjson.dumps({'ver': '3', 'cmd': ChatCommands.PING}),
                http_proxy_host=self.proxy_host,
                http_proxy_port=self.proxy_port
            )
        )
        self.websocket_thread.start()
        return is_live, live_id, live_title

    def _get_chat_messages_by_channel_id(self):
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

    def _get_chat_by_channel_id(self, match, params):
        return self.get_chat_by_channel_id(match.group('channel_id'), params)

    def get_channel_detail(self):
        for i in range(self.max_retries):
            try:
                live_info = self._session_get_json(self._LIVE_DETAIL_URL.format(channel_id=self.live_channel_id))['content']
                return live_info['status'] == "OPEN", live_info['liveId'], live_info['liveTitle'], live_info['chatChannelId']
            except (JSONDecodeError, RequestException) as e:
                continue
        raise UserNotFound(f'Unable to find Chzzk channel: "{self.live_channel_id}"')

    def get_chat_access_token(self):
        for i in range(self.max_retries):
            try:
                return self._session_get_json(
                    self._ACCESS_TOKEN_URL.format(chat_channel_id=self.chat_channel_id),
                    cookies=self.cookies
                )['content']['accessToken']
            except (JSONDecodeError, RequestException) as e:
                continue
        raise SiteError(f'Unable to get access token of Chzzk: "{self.chat_channel_id}"')

    def terminate(self, signum=None, frame=None):
        print("###### terminate process #######")
        self.event.set()
        self.websocket.close()
        self.websocket_thread.join(timeout=self.timeout)

    def get_chat_by_channel_id(self, channel_id, params):
        # First function to be called

        self.live_channel_id = channel_id
        self.timeout = params.get('message_receive_timeout')
        self.cookies = {
            "NID_AUT": params.get('NID_AUT', self._DEFAULT_NID_AUT),
            "NID_SES": params.get('NID_SES', self._DEFAULT_NID_SES)
        }
        if self.session.proxies:
            proxy_full = self.session.proxies.get('https')
            if proxy_full:
                proxy_splitted = proxy_full.split(':')
                self.proxy_host = proxy_splitted[0]
                self.proxy_port = proxy_splitted[1] if len(proxy_splitted) > 1 else None

        print(f'params: {params}')
        signal.signal(signal.SIGINT, self.terminate)

        # connect websocket before
        is_live, live_id, live_title = self.connect_websocket()

        return Chat(
            self._get_chat_messages_by_channel_id(),
            title=live_title,
            duration=None,
            status='live' if is_live else 'upcoming',  # Always live or upcoming
            video_type='video',
            id=live_id
        )
