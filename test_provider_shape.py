import unittest
from rapid_client import extract_users, RapidAPIError

U={'rest_id':'1234567890123456789','legacy':{'screen_name':'test_user','name':'اسم تجريبي'}}
class ProviderShapeTests(unittest.TestCase):
    def test_graphql_nested_timeline(self):
        response={'data':{'user':{'result':{'timeline_v2':{'timeline':{'instructions':[{'entries':[{'content':{'itemContent':{'user_results':{'result':U}}}},{'content':{'cursorType':'Bottom','value':'next-2'}}]}]}}}}}}
        users,cursor=extract_users(response)
        self.assertEqual(users[0]['id'],U['rest_id'])
        self.assertEqual(cursor,'next-2')
    def test_flat_followers(self):
        users,cursor=extract_users({'result':{'followers':[U]},'cursor':{'bottom':'abc'}})
        self.assertEqual(len(users),1)
        self.assertEqual(cursor,'abc')
    def test_empty_timeline(self):
        users,cursor=extract_users({'result':{'timeline':{'instructions':[]}}})
        self.assertEqual(users,[])
        self.assertIsNone(cursor)
    def test_invalid_shape_rejected(self):
        with self.assertRaisesRegex(RapidAPIError,'حقول الاستجابة'):
            extract_users({'message':'unknown format','request_id':'x'})
    def test_api_error_rejected(self):
        with self.assertRaises(RapidAPIError):extract_users({'success':False,'message':'temporarily unavailable'})
    def test_dont_parse_unrelated_users(self):
        # Without a followers response envelope, a nested user is not proof
        # of a successful followers API call.
        with self.assertRaises(RapidAPIError):extract_users({'data':{'user':{'result':U}}})
if __name__=='__main__':unittest.main()
