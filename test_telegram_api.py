import unittest
from unittest.mock import patch
from telegram_api import TelegramAPI, TelegramAPIError

class TelegramAPITests(unittest.TestCase):
 def test_rate_limit_retries_once_and_clamps_wait(self):
  responses=iter([{'ok':False,'error_code':429,'parameters':{'retry_after':99}},{'ok':True,'result':[]}])
  calls=[]
  def request(method,params,timeout):calls.append(method);return next(responses)
  api=TelegramAPI('123:secret',request_fn=request)
  with patch('telegram_api.time.sleep') as sleep:
   self.assertEqual(api.get_updates(5),[])
  self.assertEqual(calls,['getUpdates','getUpdates']);sleep.assert_called_once_with(30)
 def test_error_does_not_echo_secret_or_api_body(self):
  api=TelegramAPI('123:secret',request_fn=lambda *_:{'ok':False,'error_code':400,'description':'123:secret'})
  with self.assertRaises(TelegramAPIError) as err:api.send_message(4,'hello')
  self.assertNotIn('secret',str(err.exception))
  self.assertNotIn('hello',str(err.exception))
