"""Unit tests without a Home Assistant installation or a real AP."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('ap_test');pkg.__path__ = []
sys.modules['ap_test'] = pkg
api = types.ModuleType('ap_test.api')
api.ZyxelAuthenticationError = type('ZyxelAuthenticationError', (Exception,), {})
api.ZyxelConnectionError = type('ZyxelConnectionError', (Exception,), {})
sys.modules['ap_test.api'] = api
spec = importlib.util.spec_from_file_location('ap_test.ap_api', ROOT/'custom_components/ha_zyxel/ap_api.py')
m = importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

DATA = """var zyshdata0=[{'_model':'NWA50AX','_firmware_version':'V7.12(ABYW.0)'}];
var errno0=0;
var errmsg0='OK';
var zyshdata1=[{'_Slot':[{'__name':'1','_wlanReceivedByte':'2022151059','_wlanTransmittedByte':'331950669','_TxPower':'19','_Channel_Utilization':'78'},{'__name':'2','_wlanReceivedByte':'307302837','_wlanTransmittedByte':'1546869915','_TxPower':'23','_Channel_Utilization':'21'}]}];
var errno1=0;
var zyshdata2=[{'_Slot1':'6 (20 MHz)','_Slot2':'44 (80 MHz)'}];
var errno2=0;
"""

def response(text, status=200, headers=None):
    return types.SimpleNamespace(text=text,status_code=status,headers=headers or {})

class APTests(unittest.TestCase):
    def test_radio_mapping(self):
        data = m.normalize_status(m.parse_response(DATA))
        self.assertEqual(data['radios']['radio_2g']['BytesReceived'],2022151059)
        self.assertEqual(data['radios']['radio_5g']['ChannelUtilization'],21)
        self.assertEqual(data['radios']['radio_5g']['Channel'],'44 (80 MHz)')
    def test_failed_command_is_omitted(self):
        data=m.normalize_status(m.parse_response(DATA.replace('errno1=0','errno1=1')))
        self.assertNotIn('BytesReceived',data['radios']['radio_2g'])
    def test_reject_executable_code(self):
        with self.assertRaises(api.ZyxelConnectionError):
            m.parse_response("var zyshdata0=__import__('os').system('false');")
    def test_login_page_is_not_data(self):
        with self.assertRaises(api.ZyxelConnectionError):m.parse_response('<html>Login</html>')
    def test_nwa50be_accepted(self):
        data=m.normalize_status(m.parse_response(DATA.replace("'NWA50AX'","'NWA50BE'")))
        self.assertEqual(data['device']['model'],'NWA50BE')
        self.assertEqual(data['radios']['radio_5g']['TxPower'],23)
    def test_6ghz_slot(self):
        data=m.normalize_status(m.parse_response(DATA.replace("'_Slot2':'44 (80 MHz)'","'_Slot2':'44 (80 MHz)','_Slot3':'37 (320 MHz)'")))
        self.assertEqual(data['radios']['radio_6g']['Channel'],'37 (320 MHz)')
    def test_wrong_model(self):
        with self.assertRaises(api.ZyxelConnectionError):m.normalize_status(m.parse_response(DATA.replace('NWA50AX','OTHER')))
    def test_csrf(self):
        form=m.LoginForm();form.feed("<input value='a&amp;b' name='CSRFToken' type='hidden'>")
        self.assertEqual(form.csrf,'a&b')
    def test_bad_host(self):
        for host in ['https://user:secret@example.com','ftp://example.com','http://example.com/path']:
            with self.assertRaises(api.ZyxelConnectionError):m.NWA50AXClient(host,'admin','secret')
    def test_login_and_status(self):
        c=m.NWA50AXClient('http://ap.example','admin','secret')
        def request(method,path,**kw):
            if method=='GET':return response('<input name="CSRFToken" value="example">')
            if path=='/':
                self.assertEqual(kw['data']['pwd'],kw['data']['password'])
                self.assertEqual(kw['data']['CSRFToken'],'example')
                c.session.cookies.set('authtok','test')
                return response('',302)
            self.assertEqual(kw['data'], [
                ('filter', 'js2'),
                ('cmd', 'show version'),
                ('cmd', 'show wlan all'),
                ('cmd', 'show wireless-hal current channel'),
                ('cmd', 'show wireless-hal statistic'),
                ('cmd', 'show fqdn'),
                ('cmd', 'show nebula cloud status'),
                ('write', '0'),
            ])
            return response(DATA)
        c._request=Mock(side_effect=request)
        self.assertEqual(c.fetch_status()['device']['model'],'NWA50AX')
        c.close()
    def test_https_redirect(self):
        c=m.NWA50AXClient('http://ap.example','admin','secret','ab'*32)
        c._follow_https_redirect('https://ap.example/')
        self.assertEqual(c.host,'https://ap.example')
        for bad in ['https://evil.example/','http://ap.example/','https://ap.example/x']:
            c.host='http://ap.example'
            with self.assertRaises(api.ZyxelConnectionError):c._follow_https_redirect(bad)
        c.close()
    def test_https_redirect_without_pin_asks_for_https(self):
        c=m.NWA50AXClient('http://ap.example','admin','secret')
        with self.assertRaises(api.ZyxelConnectionError):c._follow_https_redirect('https://ap.example/')
        self.assertEqual(c.host,'https://ap.example')
        c.close()
    def test_wrong_credentials(self):
        c=m.NWA50AXClient('http://ap.example','admin','secret')
        c._request=Mock(side_effect=[response('<input name="CSRFToken" value="x">'),response('Login denied')])
        with self.assertRaises(api.ZyxelAuthenticationError):c.fetch_status()
        c.close()
    def test_expired_session_retries_once(self):
        c=m.NWA50AXClient('http://ap.example','admin','secret')
        c._login=Mock(side_effect=lambda:setattr(c,'sessionkey',True))
        c._request=Mock(return_value=response('',302))
        with self.assertRaises(api.ZyxelAuthenticationError):c.fetch_status()
        self.assertEqual(c._login.call_count,2)
        self.assertEqual(c._request.call_count,2)
        c.close()

if __name__=='__main__':unittest.main()
