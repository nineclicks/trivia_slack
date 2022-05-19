"""
Module for SlackTrivia class
"""
import json
import time
import logging

from slack_sdk.rtm_v2 import RTMClient
from slack_sdk.errors import SlackApiError

from trivia_core import TriviaCore

logging.basicConfig(level = 'INFO')

NAME_CACHE_SECONDS = 12 * 60 * 60

class SlackTrivia:
    """
    Slack implementation of Trivia Bot
    """

    def __init__(self):
        with open('config.json', 'r', encoding='utf-8') as pointer:
            self._config = json.load(pointer)

        self._names_cache = {}
        self._client = RTMClient(token=self._config['slack_bot_token'])
        self._trivia = TriviaCore(**self._config['trivia_core'], platform=self._team_id())
        self._init_handlers()
        self._client.start()

    def _team_id(self):
        return self._client.web_client.team_info()['team']['id']

    def format_question(self, question):
        """
        Format a question dict as returned from TriviaCore
        """

        user = question.get('winning_user')
        if user:
            username = self.get_display_name(user.get('uid',''))
            line1 = f'Correct: *{question["winning_answer"]}* '
            line1 += f'-- {username} (today: {user["score"]:,} #{user["rank"]})'
        else:
            line1 = f'Answer: *{question["winning_answer"]}*'

        line2 = f'({question["year"]}) *{question["category"]}* '
        line2 += f'for *{question["value"]}*'
        if question['comment']:
            line2 += f' _{question["comment"]}_'

        line3 = f'>{question["question"]}'

        return f'{line1}\n{line2}\n{line3}'

    def post_message(self, **message_args):
        # pylint: disable=broad-except
        """
        Post a message to Slack
        """

        tries = 0
        while tries < max(self._config['max_tries'], 1):
            tries += 1
            try:
                return self._client.web_client.chat_postMessage(**message_args)
            except Exception as ex:
                logging.exception(ex)
                logging.error('Slack send error. Try # %s', str(tries))
                time.sleep(1)

    def get_display_name(self, uid:str) -> str:
        """
        Gets the display name or cached display name of a Slack uid
        """
        name, name_time = self._names_cache.get(uid, ('???', 0))

        if time.time() > name_time + NAME_CACHE_SECONDS:
            name_priority = [
                'display_name_normalized',
                'real_name_normalized',
            ]
            logging.info('Getting username for uid: %s', uid)

            name = None

            try:
                user_info = self._client.web_client.users_info(user=uid)

                user = user_info['user']['profile']
                for name_type in name_priority:
                    if (name_type in user
                            and user[name_type] is not None
                            and user[name_type] != ''):
                        name = user[name_type]
                        break

            except SlackApiError:
                pass # There is no user

        if not name:
            name = '(no user)'

        self._names_cache[uid] = (name, time.time())
        return name

    def _init_handlers(self):

        @self._trivia.on_pre_format
        def pre_format(message):
            return f'```{message}```'

        @self._trivia.on_post_question
        def show_question(question):
            text = self.format_question(question)
            print(question)
            message_args = {
                'channel': self._config['trivia_channel'],
                'text': text,
                **self._config['bot']
            }

            self.post_message(**message_args)

        @self._trivia.on_post_message
        def show_message(message):
            message_args = {
                'channel': self._config['trivia_channel'],
                'text': message,
                **self._config['bot']
            }

            self.post_message(**message_args)

        @self._trivia.on_post_reply
        def show_reply(message, message_payload):
            message_args = {
                'channel': message_payload['channel'],
                'text': message,
                **self._config['bot']
            }

            self.post_message(**message_args)

        @self._trivia.on_get_display_name
        def get_display_name(uid):
            return self.get_display_name(uid)

        @self._trivia.on_correct_answer
        def correct_answer(message_payload, _):
            try:
                self._client.web_client.reactions_add(
                    channel = self._config['trivia_channel'],
                    name = 'white_check_mark',
                    timestamp = message_payload['ts'],
                )
            except Exception as ex:
                logging.exception(ex)

        @self._client.on('message')
        def handle_message(_: RTMClient, event: dict):
            if(
               event['type'] != 'message'
               or 'subtype' in event
               or 'thread_ts' in event
               or (event['channel'] != self._config['trivia_channel'] and
                   event['user'] != self._config['trivia_core']['admin_uid'])
               ):
                return

            self._trivia.handle_message(
                    uid = event['user'],
                    text = event['text'],
                    message_payload = event,
                    )

        @self._trivia.on_error
        def error(message_payload, text):
            user = message_payload['user']
            ts = message_payload['ts']
            channel = message_payload['channel']
            self._client.web_client.reactions_add(
                channel=channel,
                name='x',
                timestamp=ts
            )
            self._client.web_client.chat_postEphemeral(
                channel=channel,
                user=user,
                text=text,
                **self._config['bot']
                )

slack = SlackTrivia()
