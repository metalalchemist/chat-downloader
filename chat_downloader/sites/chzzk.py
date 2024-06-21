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
import json
from hashlib import sha256
from requests.exceptions import RequestException
from json.decoder import JSONDecodeError
from threading import Thread, Event

# NOTE: https://github.com/kimcore/chzzk/blob/main/src/chat/chat.ts


class ChzzkChatWSS:
    CHAT_CMD = {
        'ping': 0,
        'pong': 10000,
        'connect': 100,
        'connected': 10100,
        'send_chat': 3101,
        'request_recent_chat': 5101,
        'response_recent_chat': 15101,
        'event': 93006,
        'chat': 93101,
        'donation': 93102,
        'kick': 94005,
        'block': 94006,
        'blind': 94008,
        'notice': 94010,
        'penalty': 94015,
    }

    def __init__(self, channel_id: str, access_token: str, timeout=5):
        self.cid = channel_id
        self.accTkn = access_token
        self.timeout = timeout
        self.num_connect = 0
        self.server_id = sum([ord(c) for c in self.cid]) % 9 + 1
        self._keep_alive_thread = None
        self._close_event = Event()

        self.connect()

    def connect(self):
        # create new socket
        self.socket = websocket.WebSocket()
        self.set_timeout(self.timeout)

        # start connection
        self.socket.connect(f'wss://kr-ss{self.server_id}.chat.naver.com/chat')

        send_dict = {
            "ver": "3",
            "svcid": "game",
            "cid": self.cid,
            "cmd": self.CHAT_CMD['connect'],
            "tid": 1,
            "bdy": {
                "uid": None,
                "devType": 2001,
                "accTkn": self.accTkn,
                "auth": "READ"
            }
        }
        self.send(send_dict)
        sock_response = self.recv()

        self.sid = sock_response['bdy']['sid']

        send_dict['cmd'] = self.CHAT_CMD['request_recent_chat']
        send_dict['tid'] += 1
        send_dict['sid'] = self.sid
        send_dict['bdy'] = {
            'recentMessageCount': 50
        }
        self.send(send_dict)

        if not self.socket.connected:
            raise SiteError('Chzzk websocket connection failed!')

        log('debug', 'Chzzk websocket connected successfully...')
        self.num_connect += 1

        if not self._keep_alive_thread:
            self._keep_alive_thread = Thread(target=self._keep_alive, daemon=True)
            self._keep_alive_thread.start()

    def _keep_alive(self):
        while not self._close_event.wait(20.0):
            try:
                self.send({'ver': '3', 'cmd': self.CHAT_CMD['ping']})
            except:
                pass

    def send(self, obj):
        self.socket.send(json.dumps(obj))

    def recv(self):
        return json.loads(self.socket.recv())

    def set_timeout(self, message_receive_timeout):
        self.socket.settimeout(message_receive_timeout)

    def close_connection(self):
        self.socket.close()

    def close(self):
        self._close_event.set()
        self.close_connection()


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

    def _get_chat_by_video_id(self, match, params):
        # TODO: vod support
        return

    def _get_chat_messages_by_channel_id(self, chat_channel_id, chat_access_token, params):
        socket = ChzzkChatWSS(channel_id=chat_channel_id,
                              access_token=chat_access_token,
                              timeout=params.get('message_receive_timeout'))
        message_count = 0
        try:
            while True:
                try:
                    raw_msg = socket.recv()
                except KeyboardInterrupt:
                    break

                except websocket._exceptions.WebSocketTimeoutException as e:
                    yield {}
                    continue

                except Exception as e:
                    log('error', e)

                    socket.close_connection()
                    socket.connect()
                    continue

                if raw_msg.get('cmd') == socket.CHAT_CMD['ping']:
                    socket.send({'ver': '3', 'cmd': socket.CHAT_CMD['pong']})
                    continue

                if raw_msg.get('cmd') == socket.CHAT_CMD['pong']:
                    continue

                if socket.num_connect > 1 and raw_msg.get('cmd') == socket.CHAT_CMD['response_recent_chat']:
                    log('debug', 'Not the first connection, so the response of recent chat will be ignored...')
                    yield {}
                    continue

                if "bdy" not in raw_msg:
                    continue

                raw_body = raw_msg['bdy']
                if isinstance(raw_body, list):
                    chat_msgs = raw_body
                elif isinstance(raw_body, dict):
                    chat_msgs = raw_body.get('messageList', [raw_body])
                else:
                    continue

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

                    try:
                        chat_msg['profile'] = json.loads(chat_msg['profile']) if chat_msg.get('profile') else {}
                        display_name = chat_msg['profile'].get('nickname', '')

                        chat_msg['extras'] = json.loads(chat_msg['extras']) if chat_msg.get('extras') else {}
                        emotes = chat_msg['extras'].get('emojis')
                        pay_amount = chat_msg['extras'].get('payAmount')

                        data = {}
                        data['timestamp'] = chat_msg[msgTime] * 1000
                        data['message_id'] = sha256(json.dumps(chat_msg, sort_keys=True).encode('utf8')).hexdigest()
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

                        message_count += 1
                        yield data
                        log('debug', f'Total number of messages: {message_count}')

                    except Exception as e:
                        log('error', e)

        finally:
            socket.close()

    def _get_chat_by_channel_id(self, match, params):
        return self.get_chat_by_channel_id(match.group('channel_id'), params)

    def get_chat_by_channel_id(self, channel_id, params):
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
