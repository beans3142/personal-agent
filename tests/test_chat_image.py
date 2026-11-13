import base64
import io
import json
from types import SimpleNamespace

import pytest

from agent import chat_image


def png_bytes(size=(4, 4)):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', size, (200, 100, 50)).save(buffer, 'PNG')
    return buffer.getvalue()


def webp_bytes():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), (1, 2, 3)).save(buffer, 'WEBP')
    return buffer.getvalue()


def test_image_is_saved_as_png_in_the_outbox(tmp_path):
    name = chat_image.save_png(png_bytes(), tmp_path)
    assert name.startswith('image-') and name.endswith('.png')
    assert (tmp_path / name).read_bytes().startswith(b'\x89PNG')


def test_other_formats_are_converted_to_png(tmp_path):
    name = chat_image.save_png(webp_bytes(), tmp_path)
    assert (tmp_path / name).read_bytes().startswith(b'\x89PNG')


def test_non_images_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        chat_image.save_png(b'<html>login</html>', tmp_path)


@pytest.mark.parametrize("prompt", ["", "   ", "a" * 2001])
def test_prompt_size_is_bounded(prompt):
    with pytest.raises(ValueError):
        chat_image.request_text(prompt)


def test_prompt_asks_for_an_image_explicitly():
    text = chat_image.request_text('귀여운 고양이')
    assert '귀여운 고양이' in text and '이미지' in text


def test_tool_runs_the_ui_in_a_subprocess_and_returns_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_OUTBOX', str(tmp_path))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, json.loads(kwargs['input'])))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'image': base64.b64encode(png_bytes()).decode()}))
    tool = chat_image.build_image_tools(run=run)[0]
    result = json.loads(tool.run({'prompt': '고양이'}))
    assert result['status'] == 'generated' and (tmp_path / result['file']).exists()
    assert calls[0][0][-1] == '--image' and calls[0][1] == {'prompt': chat_image.request_text('고양이')}


def test_tool_reports_failure_without_inventing_an_image(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_OUTBOX', str(tmp_path))
    tool = chat_image.build_image_tools(run=lambda argv, **kw: SimpleNamespace(returncode=1, stdout=''))[0]
    result = json.loads(tool.run({'prompt': '고양이'}))
    assert result['status'] == 'failed' and not list(tmp_path.iterdir())
