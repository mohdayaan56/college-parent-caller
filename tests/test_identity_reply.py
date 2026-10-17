import unittest
from unittest.mock import patch
from app import llm


class IdentityReplyTests(unittest.TestCase):
    def test_general_confirmation_keeps_unknown_and_wrong_identity_closed(self):
        cases = [({'status':'confirmed','claimed_name':''},'Go ahead, you reached me','confirmed'),
                 ({'status':'confirmed','claimed_name':'Other Person'},'I am Other Person','denied'),
                 ({'status':'confirmed','claimed_name':'Demo Parent'},'What are the marks?','uncertain'),
                 ({'status':'uncertain','claimed_name':''},'Who is calling?','uncertain'),
                 ({'status':'denied','claimed_name':''},'Wrong person','denied'),
                 ({'status':'anything','claimed_name':''},'Unrelated reply','uncertain')]
        with patch.object(llm.settings,'ollama_enabled',True):
            for data,reply,expected in cases:
                with self.subTest(reply=reply),patch.object(llm,'_chat_json',return_value=data):
                    self.assertEqual(llm.classify_identity_reply(reply,['Demo Parent']),expected)

    def test_missing_model_result_requires_clarification(self):
        with patch.object(llm.settings,'ollama_enabled',True),patch.object(llm,'_chat_json',return_value=None):
            self.assertEqual(llm.classify_identity_reply('Anything',['Demo Parent']),'uncertain')
