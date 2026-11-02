import json
from types import SimpleNamespace

import pytest

from agent.android_control import AndroidControl, UiaDevice, build_android_tools, parse_nodes


XML = '''<hierarchy><node text="카카오톡" package="com.kakao.talk" bounds="[0,0][100,40]"/>
<node text="" class="android.widget.EditText" resource-id="chat" clickable="true" bounds="[0,40][200,80]"/>
<node text="secret" password="true" resource-id="password" bounds="[0,80][200,120]"/>
<node text="결제하기" clickable="true" bounds="[0,120][200,160]"/></hierarchy>'''


class Device:
    def __init__(self):
        self.xml = XML
        self.calls = []

    def dump_hierarchy(self):
        return self.xml

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def set_text(self, target, value):
        self.calls.append(("text", target["id"], value))

    def key(self, key):
        self.calls.append(("key", key))


def control(**kwargs):
    device = Device()
    return AndroidControl(device, **kwargs), device


def test_password_is_redacted_but_unicode_and_bounds_remain():
    nodes = parse_nodes(XML)
    assert "secret" not in json.dumps(nodes)
    assert nodes[0]["text"] == "카카오톡"
    assert nodes[1]["bounds"] == [0, 40, 200, 80]
    assert nodes[2]["password"] is True


def test_click_requires_observation_and_checks_current_target():
    ui, device = control()
    state = ui.observe()
    device.xml = XML.replace('resource-id="chat"', 'resource-id="different"')
    with pytest.raises(ValueError, match="changed"):
        ui.click(state["snapshot"], 1)
    assert device.calls == []


def test_click_uses_observed_bounds():
    ui, device = control()
    state = ui.observe()
    ui.click(state["snapshot"], 1)
    assert device.calls == [("click", 100, 60)]


def test_unrelated_inserted_node_does_not_change_the_selected_target():
    ui, device = control()
    state = ui.observe()
    device.xml = XML.replace('<hierarchy>', '<hierarchy><node text="clock" bounds="[200,0][250,40]"/>')
    ui.input(state["snapshot"], 1, text="search")
    assert device.calls == [("text", 2, "search")]


def test_observation_hides_anonymous_containers_with_labelled_children():
    ui, device = control()
    device.xml = '<hierarchy><node clickable="true" bounds="[0,0][900,1400]"><node text="Search" bounds="[0,0][300,50]"/></node></hierarchy>'
    assert [node["id"] for node in ui.observe()["nodes"]] == [1]


def test_unrelated_banner_changes_do_not_invalidate_labelled_target():
    ui, device = control()
    device.xml = '<hierarchy><node clickable="true" bounds="[0,0][900,1400]"><node text="Search" bounds="[0,0][300,50]"/><node text="banner A" bounds="[0,200][900,400]"/></node></hierarchy>'
    state = ui.observe()
    device.xml = device.xml.replace('banner A', 'banner B')
    ui.click(state["snapshot"], 1, expected_label="Search")
    assert device.calls == [("click", 150, 25)]


def test_changed_consequential_label_in_large_parent_still_invalidates_target():
    ui, device = control()
    device.xml = '<hierarchy><node clickable="true" bounds="[0,0][900,1400]"><node text="Search" bounds="[0,0][300,50]"/><node text="banner A" bounds="[0,200][900,400]"/></node></hierarchy>'
    state = ui.observe()
    device.xml = device.xml.replace('banner A', '결제하기')
    with pytest.raises(ValueError, match="changed"):
        ui.click(state["snapshot"], 1, expected_label="Search")
    assert not device.calls


def test_click_rejects_array_position_when_expected_label_does_not_match_id():
    ui, device = control()
    state = ui.observe()
    with pytest.raises(ValueError, match="label"):
        ui.click(state["snapshot"], 0, expected_label="결제하기")
    assert device.calls == []


def test_observation_exposes_checkbox_state_and_click_tool_requires_label():
    ui, device = control()
    device.xml = '<hierarchy><node text="Required" checkable="true" checked="true" clickable="true" bounds="[0,0][100,40]"/></hierarchy>'
    assert ui.observe()["nodes"][0]["checked"] is True
    click = next(tool for tool in build_android_tools(ui) if tool.name == "android_click")
    assert "expected_label" in click.parameters["required"]


def test_korean_input_is_replaced_in_the_selected_field():
    ui, device = control()
    state = ui.observe()
    ui.input(state["snapshot"], 1, text="자비스 테스트")
    assert device.calls == [("text", 1, "자비스 테스트")]


def test_credentials_come_from_environment_without_entering_result():
    ui, device = control(env={"KAKAO_PASSWORD": "private-password"})
    state = ui.observe()
    result = ui.input(state["snapshot"], 2, credential="KAKAO_PASSWORD")
    assert device.calls == [("text", 2, "private-password")]
    assert "private-password" not in json.dumps(result)
    with pytest.raises(ValueError):
        ui.input(state["snapshot"], 2, credential="AWS_SECRET_ACCESS_KEY")


def test_empty_optional_text_does_not_block_credential_reference():
    ui, device = control(env={"KAKAO_PASSWORD": "private-password"})
    state = ui.observe()
    ui.input(state["snapshot"], 2, text="", credential="KAKAO_PASSWORD")
    assert device.calls == [("text", 2, "private-password")]


def test_english_android_permission_allow_requires_owner_approval():
    ui, device = control()
    device.xml = '<hierarchy><node text="WHILE USING THE APP" resource-id="com.android.permissioncontroller:id/permission_allow_foreground_only_button" package="com.android.permissioncontroller" clickable="true" bounds="[0,0][200,80]"/></hierarchy>'
    state = ui.observe()
    with pytest.raises(PermissionError):
        ui.click(state["snapshot"], 0, expected_label="WHILE USING THE APP")
    assert device.calls == []


def test_device_error_does_not_echo_credentials_to_model():
    ui, device = control(env={"KAKAO_PASSWORD": "private-password"})
    state = ui.observe()

    def fail(index, value):
        raise RuntimeError("rpc rejected " + value)

    device.set_text = fail
    with pytest.raises(RuntimeError) as caught:
        ui.input(state["snapshot"], 2, credential="KAKAO_PASSWORD")
    assert "private-password" not in str(caught.value)


def test_payment_cannot_be_clicked_without_owner_approval():
    asked = []
    ui, device = control(approve=lambda name, args: asked.append((name, args)) or False)
    state = ui.observe()
    with pytest.raises(PermissionError):
        ui.click(state["snapshot"], 3)
    assert asked
    assert device.calls == []


def test_payment_label_on_child_also_protects_parent_button():
    ui, device = control()
    device.xml = '<hierarchy><node clickable="true" bounds="[0,0][200,80]"><node text="결제하기" bounds="[10,10][190,70]"/></node></hierarchy>'
    state = ui.observe()
    with pytest.raises(PermissionError):
        ui.click(state["snapshot"], 0)
    assert device.calls == []


def test_payment_label_on_parent_also_protects_child_text():
    ui, device = control()
    device.xml = '<hierarchy><node text="결제하기" clickable="true" bounds="[0,0][200,80]"><node text="10000 KRW" clickable="false" bounds="[10,10][190,70]"/></node></hierarchy>'
    state = ui.observe()
    with pytest.raises(PermissionError):
        ui.click(state["snapshot"], 1)
    assert device.calls == []


def test_current_state_is_rechecked_after_approval():
    ui, device = control()
    state = ui.observe()

    def approve(*_):
        device.xml = XML.replace("결제하기", "다른 주문 결제하기")
        return True

    ui.approve = approve
    with pytest.raises(ValueError, match="changed"):
        ui.click(state["snapshot"], 3)
    assert device.calls == []


def test_changed_child_of_stable_parent_requires_new_observation():
    ui, device = control()
    device.xml = '<hierarchy><node clickable="true" bounds="[0,0][200,80]"><node text="Continue" bounds="[10,10][190,70]"/></node></hierarchy>'
    state = ui.observe()
    device.xml = device.xml.replace('Continue', '결제하기')
    with pytest.raises(ValueError, match="changed"):
        ui.click(state["snapshot"], 0)
    assert device.calls == []


def test_observation_redacts_configured_values_even_when_password_is_revealed():
    ui, device = control(env={"KAKAO_ID": "private@example.com", "KAKAO_PASSWORD": "revealed-password"})
    device.xml = '<hierarchy><node text="private@example.com" bounds="[0,0][200,80]"/><node text="revealed-password" password="false" bounds="[0,80][200,160]"/></hierarchy>'
    result = json.dumps(ui.observe())
    assert "private@example.com" not in result
    assert "revealed-password" not in result


def test_tools_are_available_to_existing_agent_loop():
    ui, _ = control()
    names = {tool.name for tool in build_android_tools(ui)}
    assert {"android_open", "android_screen", "android_click", "android_input", "android_swipe", "android_key"} <= names


def test_plain_text_and_credential_tools_have_separate_required_arguments():
    ui, _ = control()
    tools = {tool.name: tool for tool in build_android_tools(ui)}
    assert "credential" not in tools["android_input"].parameters["properties"]
    assert "text" in tools["android_input"].parameters["required"]
    assert "text" not in tools["android_credential"].parameters["properties"]
    assert "credential" in tools["android_credential"].parameters["required"]


def test_keyboard_submit_requires_fresh_focused_input():
    ui, device = control()
    state = ui.observe()
    with pytest.raises(ValueError, match="focused"):
        ui.submit(state["snapshot"], 1)
    assert not device.calls
    device.xml = XML.replace('resource-id="chat"', 'resource-id="chat" focused="true"')
    state = ui.observe()
    ui.submit(state["snapshot"], 1)
    assert device.calls == [("key", "enter")]


def test_real_adapter_uses_native_set_text_without_keyboard_install():
    calls = []

    class Native:
        def dump_hierarchy(self):
            return XML

        def __call__(self, **selector):
            calls.append(selector)
            return self

        def set_text(self, value):
            calls.append(value)

    adapter = UiaDevice()
    adapter._device = Native()
    target = parse_nodes(XML)[1]
    target["id"] = 2  # Index from the previous hierarchy must not select password.
    adapter.set_text(target, "한글 입력")
    assert calls == [{"className": "android.widget.EditText", "resourceId": "chat", "instance": 0}, "한글 입력"]


def test_open_uses_resolved_launcher_and_reports_launch_errors():
    calls = []

    class Native:
        def app_list(self):
            return ["com.kakao.talk"]

        def shell(self, command):
            calls.append(command)
            if command[:2] == ["cmd", "package"]:
                return SimpleNamespace(output="priority=0\ncom.kakao.talk/.activity.SplashActivity\n", exit_code=0)
            return SimpleNamespace(output="Error: Activity not started", exit_code=1)

    adapter = UiaDevice()
    adapter._device = Native()
    with pytest.raises(RuntimeError, match="Activity not started"):
        adapter.open("kakao")
    assert calls[-1] == ["am", "start", "-W", "-n", "com.kakao.talk/.activity.SplashActivity"]


def test_screenshot_uses_default_android_display_not_display_id_zero(tmp_path, monkeypatch):
    from agent import android_portal

    monkeypatch.setattr(android_portal.Android, "screen", lambda self: b"\x89PNG\r\n\x1a\nimage")
    adapter = UiaDevice("device:5555")
    path = tmp_path / "screen.png"
    adapter.screenshot(path)
    assert path.read_bytes().startswith(b"\x89PNG")
