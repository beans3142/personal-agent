import base64
import json
from mcp import types
from agent.loop import Tool
from agent.mcp_server import ToolBridge, scoped_allowlist
from agent.phone_tools import build_phone_tools
from agent.device_nodes import DeviceStore


def test_bridge_preserves_native_image_content():
    expected=types.CallToolResult(content=[types.TextContent(type='text',text='screen'),
        types.ImageContent(type='image',data=base64.b64encode(b'image').decode(),mimeType='image/png')])
    tool=Tool('phone_screenshot','screen',{'type':'object','properties':{}},lambda _:expected)
    result=ToolBridge([tool],{'phone_screenshot'},approve=lambda *_:True).call('phone_screenshot',{})
    assert result == expected


def test_phone_tools_are_private_in_public_channels():
    names={'phone_screen','phone_screenshot','phone_tap','phone_swipe','phone_type','phone_key','phone_open','phone_scroll'}
    assert not scoped_allowlist(names,{'DAEVIS_MCP_PUBLIC':'1'})


def test_swipe_and_long_press_are_generic_device_actions(tmp_path):
    store=DeviceStore(tmp_path/'jobs.sqlite3')
    store.enroll('phone-test',['phone_task'])
    for op,args in [('swipe',{'x1':100,'y1':900,'x2':100,'y2':200,'duration':500}),('long_press',{'x':20,'y':30}),('screenshot',{})]:
        store.enqueue('phone-test','phone_task',{'op':op,'args':args},op,60)


def test_screen_capture_returns_pixels_to_model_not_a_filename():
    png=b'\x89PNG\r\n\x1a\n'+b'test'
    calls=[]
    def phone(op,args):
        calls.append((op,args))
        return {'image_file':'f1','screen':{'w':1080,'h':2400,'nodes':[]}}
    tools={t.name:t for t in build_phone_tools(phone,lambda *_:True,image_download=lambda _:('screen.png',png))}
    result=tools['phone_screenshot'].run({})
    assert isinstance(result,types.CallToolResult)
    assert base64.b64decode(result.content[1].data)==png
    assert json.loads(result.content[0].text)['screen']['w']==1080


def test_capture_failure_is_explicit_without_claiming_agent_can_see():
    tools={t.name:t for t in build_phone_tools(lambda *_:{'capture_error':'secure_window'},lambda *_:True,image_download=lambda _:None)}
    assert tools['phone_screenshot'].run({}).startswith('error:')


def test_generic_gesture_tools_forward_coordinates():
    calls=[]
    def phone(op,args): calls.append((op,args));return {'ok':True}
    tools={t.name:t for t in build_phone_tools(phone,lambda *_:True)}
    tools['phone_swipe'].run({'x1':10,'y1':900,'x2':10,'y2':200,'duration':500})
    tools['phone_long_press'].run({'x':100,'y':200,'duration':700})
    assert [c[0] for c in calls]==['swipe','long_press']
