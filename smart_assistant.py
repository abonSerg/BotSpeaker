#!/usr/bin/env python3
# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import locale
import logging
import signal
import sys
import requests
import time
import threading
import pygame
import json
from datetime import datetime
from subprocess import call
from aiy.voice.audio import AudioFormat, play_wav, record_file, Recorder
from aiy.assistant.grpc import AssistantServiceClientWithLed
from aiy.board import Board
import mod.snowboydecoder as snowboydecoder
from aiy.board import Board, Led


class AzureSpeechService:
    def __init__(self, token_url, stt_url, tts_url, subscription_key):
        self.token_url = token_url
        self.stt_url = stt_url
        self.tts_url = tts_url
        self.subscription_key = subscription_key

    def ComplexHandler(Obj):
        if hasattr(Obj, 'jsonable'):
            return Obj.jsonable()
        else:
            raise 'Object of type %s with value of %s is not JSON serializable' % (type(Obj), repr(Obj))

    def get_token(self):
        headers = {'Content-type': 'application/x-www-form-urlencoded',
                   'Content-Length': '0', 'Ocp-Apim-Subscription-Key': self.subscription_key}

        response = requests.post(self.token_url, headers=headers)

        return 'Bearer ' + response.text

    def convert_text_to_audio(self, text):
        # url2 = 'https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1'

        bearer_token = get_token()

        body = "<speak version='1.0' xml:lang='en-US'><voice xml:lang='en-US' xml:gender='Female' " \
               "name='en-US-JessaRUS'>" + text + "</voice></speak> "

        response = requests.post(self.tts_url, data=body, headers={'Authorization': bearer_token,
                                                                   'Content-Type': 'application/ssml+xml',
                                                                   'X-Microsoft-OutputFormat': 'riff-16khz-16bit-mono-pcm'})
        if response.status_code == 200:
            now = datetime.now()
            time_stamp = now.strftime('%f')
            file_name = 'sample' + time_stamp + '.wav'
            with open(file_name, 'wb') as audio:
                audio.write(response.content)
                return file_name

        return ''

    def stream_audio_file(self, file_name, chunk_size=1024):
        with open(file_name, 'rb') as f:
            while 1:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data

    def convert_audio_to_text(self, file_name):
        bearer_token = get_token()

        headers = {
            'Accept': 'application/json',
            'Transfer-Encoding': 'chunked',
            'Content-type': 'audio/wav; codec=audio/pcm; samplerate=16000',
            'Authorization': bearer_token
        }

        response = requests.post(self.stt_url, headers=headers, data=stream_audio_file(file_name))

        results = json.loads(response.content.decode('utf-8'))

        return results['DisplayText']


class ChatBotService:
    def __init__(self, bot_url, secret):
        self.bot_url = bot_url
        self.secret = secret

    class Message:
        def __init__(self, id, text):
            self.type = 'message'
            self.from_ = From(id)
            self.text = text

        def jsonable(self):
            return self.__dict__

    class From:
        def __init__(self, id):
            self.id = id

        def jsonable(self):
            return self.__dict__

    def create_bot_conversation(self):
        bot_url = self.bot_url + '/directline/conversations'

        headers = {
            'Content-type': 'application/json; charset=utf-8',
            'Authorization': 'Bearer ' + self.secret
        }

        response = requests.post(bot_url, headers=headers)
        logging.info(response.status_code)

        results = json.loads(response.content.decode('utf-8'))
        return results['conversationId']

    @staticmethod
    def get_watermark_from_directline_response(response_json: object) -> object:
        id = response_json['id']
        watermark = id.split('|', 1)[-1]
        watermark = watermark.strip("0")

        if watermark == '':
            watermark = '0'

        return watermark

    @staticmethod
    def get_response_text(response_json: object) -> object:
        text = ''
        for activity in response_json['activities']:
            if activity['type'] == 'message':
                text = text + activity['text']

        return text

    def talk_with_bot(self, text: object, user_id, conversation_id: object) -> object:
        bot_url = self.bot_url + '/directline/conversations/' + conversation_id + '/activities'

        message = Message(user_id, text)

        headers = {
            'Content-type': 'application/json; charset=utf-8',
            'Authorization': 'Bearer ' + self.secret
        }

        messageJson = json.dumps(message, default=ComplexHandler)
        messageJson = messageJson.replace('from_', 'from')

        #get watermarks
        response = requests.post(bot_url, headers=headers, data=messageJson)

        response_json = json.loads(response.content.decode('utf-8'))

        watermark = get_watermark_from_directline_response(response_json)

        bot_url = bot_url + '?watermark=' + watermark

        #get answers
        response = requests.get(bot_url, headers=headers)

        result = json.loads(response.content.decode('utf-8'))

        bot_answer = self.get_response_text(result)

        return bot_answer


class AiyAudioRecorder:
    def __init__(self, file_type):
        self.file_type = file_type

    def record_audio_by_button_pressing(self, file_name):
        with Board() as board:
            print('Press button to start recording.')

            board.button.wait_for_press()

            done = threading.Event()
            board.button.when_pressed = done.set

            def wait():
                start = time.monotonic()
                while not done.is_set():
                    duration = time.monotonic() - start
                    print('Recording: %.02f seconds [Press button to stop]' % duration)
                    time.sleep(0.5)

            record_file(AudioFormat.CD, filename=file_name, wait=wait, filetype=self.file_type)


def main():
    logging.basicConfig(level=logging.DEBUG)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

    azure_token_url = 'https://cognitiveservicesallinone.cognitiveservices.azure.com/sts/v1.0/issueToken'
    azure_stt_url = 'https://westeurope.stt.speech.microsoft.com/speech/recognition/interactive/cognitiveservices/v1' \
                    '?language=en-US&format=simple '

    azure_tts_url = 'https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1'
    azure_subscription_key = ''

    chat_bot_url = 'https://directline.botframework.com/v3'
    chat_bot_secret = ''

    model = 'WakeWord.pmdl'

    recorder_file_type = 'wav'
    conversation_file_name = 'temp.wav'
    welcome_file_name = 'temp.wav'
    user_id = ''

    detector_sensitivity = 0.55

    detector = snowboydecoder.HotwordDetector(model, detector_sensitivity)

    azure_speech_service = AzureSpeechService(azure_token_url, azure_stt_url, azure_tts_url, azure_subscription_key)
    chat_bot_service = ChatBotService(chat_bot_url, chat_bot_secret)
    audio_recorder = AiyAudioRecorder(recorder_file_type)

    while True:
        logging.info('Say a wake word')

        detector.start()

        play_wav(welcome_file_name)

        conversation_id = chat_bot_service.create_bot_conversation()

        while True:
            audio_recorder.record_audio_by_button_pressing(conversation_file_name)

            text = azure_speech_service.convert_audio_to_text(conversation_file_name)

            logging.info('Your question is:' + text)

            bot_answer = chat_bot_service.talk_with_bot(text, user_id, conversation_id)

            bot_answer_audio_file = azure_speech_service.convert_text_to_audio(bot_answer)

            play_wav(bot_answer_audio_file)


if __name__ == '__main__':
    main()


