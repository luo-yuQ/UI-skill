"""Tests for ui_image_clean_repair (Stage0 Image-2 clean repair port).

All tests are offline: provider/upload/poll/fetch/download symbols are replaced
with fakes. No real network calls.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

poc = importlib.import_module("ui_image_clean_repair")


def _make_png(path: Path, size=(64, 32), color=(200, 100, 50, 255)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def test_source_only_payload_fails_fast(tmp_path):
    source = tmp_path / "src.png"
    _make_png(source)
    with pytest.raises(ValueError, match="source_only"):
        poc.run(
            source_image=source,
            output_dir=tmp_path / "out",
            mask_overlay=None,
            mode="source_only",
            provider_base_url="https://example.invalid",
            api_key="k",
            model="gpt-image-2",
            timeout=5.0,
            prompt_file=None,
        )


def test_overlay_mode_upload_order_is_source_then_overlay(tmp_path, monkeypatch):
    source = tmp_path / "src.png"
    overlay = tmp_path / "ovl.png"
    _make_png(source)
    _make_png(overlay, color=(0, 255, 0, 120))

    upload_order = []

    def fake_upload(*, base_url, api_key, timeout, session, image_path):
        upload_order.append(image_path.name)
        return f"https://cdn.invalid/{image_path.name}"

    def fake_submit(**kwargs):
        assert kwargs["payload"]["images"] == [
            "https://cdn.invalid/src.png",
            "https://cdn.invalid/ovl.png",
        ]
        return {"data": [{"b64_json": _tiny_b64()}]}

    monkeypatch.setattr(poc, "upload_image_for_clean_repair", fake_upload)
    monkeypatch.setattr(poc, "submit_generation_for_clean_repair", fake_submit)

    result = poc.run(
        source_image=source,
        output_dir=tmp_path / "out",
        mask_overlay=overlay,
        mode="source_plus_overlay",
        provider_base_url="https://example.invalid",
        api_key="secret-key",
        model="gpt-image-2",
        timeout=5.0,
        prompt_file=None,
    )

    assert upload_order == ["src.png", "ovl.png"]
    assert result["mode"] == "source_plus_overlay"
    assert result["prompt"] == poc.SOURCE_PLUS_OVERLAY_PROMPT


def test_payload_field_set(tmp_path, monkeypatch):
    source = tmp_path / "src.png"
    overlay = tmp_path / "ovl.png"
    _make_png(source)
    _make_png(overlay)

    captured = {}

    def fake_upload(*, base_url, api_key, timeout, session, image_path):
        return f"https://cdn.invalid/{image_path.name}"

    def fake_submit(**kwargs):
        captured.update(kwargs["payload"])
        return {"data": [{"b64_json": _tiny_b64()}]}

    monkeypatch.setattr(poc, "upload_image_for_clean_repair", fake_upload)
    monkeypatch.setattr(poc, "submit_generation_for_clean_repair", fake_submit)

    poc.run(
        source_image=source,
        output_dir=tmp_path / "out",
        mask_overlay=overlay,
        mode="source_plus_overlay",
        provider_base_url="https://example.invalid",
        api_key="k",
        model="gpt-image-2",
        timeout=5.0,
        prompt_file=None,
    )

    assert set(captured.keys()) == {"model", "type", "images", "prompt", "size", "n", "response_format"}
    assert captured["model"] == "gpt-image-2"
    assert captured["type"] == "image"
    assert captured["size"] == "64x32"
    assert captured["n"] == 1
    assert captured["response_format"] == "url"


def test_api_key_redacted_in_result_debug_fields(tmp_path, monkeypatch):
    source = tmp_path / "src.png"
    overlay = tmp_path / "ovl.png"
    _make_png(source)
    _make_png(overlay)

    monkeypatch.setattr(
        poc,
        "upload_image_for_clean_repair",
        lambda **kw: f"https://cdn.invalid/{kw['image_path'].name}",
    )
    monkeypatch.setattr(
        poc,
        "submit_generation_for_clean_repair",
        lambda **kw: {"data": [{"b64_json": _tiny_b64()}]},
    )

    result = poc.run(
        source_image=source,
        output_dir=tmp_path / "out",
        mask_overlay=overlay,
        mode="source_plus_overlay",
        provider_base_url="https://example.invalid",
        api_key="super-secret",
        model="gpt-image-2",
        timeout=5.0,
        prompt_file=None,
    )

    serialized = json.dumps(result)
    assert "super-secret" not in serialized


def test_alpha_hole_inspection(tmp_path):
    from PIL import Image

    source = tmp_path / "hole.png"
    path = tmp_path / "hole.png"
    img = Image.new("RGBA", (16, 16), (255, 255, 255, 255))
    # punch a fully transparent hole
    for x in range(4, 8):
        for y in range(4, 8):
            img.putpixel((x, y), (0, 0, 0, 0))
    img.save(path)

    probe = poc.inspect_alpha_hole(path)
    assert probe["has_fully_transparent_hole"] is True
    assert probe["fully_transparent_pixels"] == 16
    assert probe["min_alpha"] == 0


def test_alpha_hole_only_run_uses_default_prompt_and_probe(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "hole.png"
    img = Image.new("RGBA", (16, 16), (255, 255, 255, 255))
    for x in range(4, 8):
        for y in range(4, 8):
            img.putpixel((x, y), (0, 0, 0, 0))
    img.save(source)

    seen_prompts = []

    def fake_upload(*, base_url, api_key, timeout, session, image_path):
        return f"https://cdn.invalid/{image_path.name}"

    def fake_submit(**kwargs):
        seen_prompts.append(kwargs["payload"]["prompt"])
        return {"data": [{"b64_json": _tiny_b64()}]}

    monkeypatch.setattr(poc, "upload_image_for_clean_repair", fake_upload)
    monkeypatch.setattr(poc, "submit_generation_for_clean_repair", fake_submit)

    result = poc.run(
        source_image=source,
        output_dir=tmp_path / "out",
        mask_overlay=None,
        mode="alpha_hole_only",
        provider_base_url="https://example.invalid",
        api_key="k",
        model="gpt-image-2",
        timeout=5.0,
        prompt_file=None,
    )

    assert seen_prompts == [poc.ALPHA_HOLE_ONLY_PROMPT]
    assert result["alpha_probe"]["has_fully_transparent_hole"] is True


def test_alpha_hole_probe_error_surfaces(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "hole.png"
    Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(source)

    monkeypatch.setattr(
        poc,
        "upload_image_for_clean_repair",
        lambda **kw: f"https://cdn.invalid/{kw['image_path'].name}",
    )
    monkeypatch.setattr(
        poc,
        "submit_generation_for_clean_repair",
        lambda **kw: {"data": [{"b64_json": _tiny_b64()}]},
    )

    def boom(path):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(poc, "inspect_alpha_hole", boom)

    with pytest.raises(RuntimeError, match="probe exploded"):
        poc.run(
            source_image=source,
            output_dir=tmp_path / "out",
            mask_overlay=None,
            mode="alpha_hole_only",
            provider_base_url="https://example.invalid",
            api_key="k",
            model="gpt-image-2",
            timeout=5.0,
            prompt_file=None,
        )


def test_async_task_protocol_routing(tmp_path, monkeypatch):
    source = tmp_path / "src.png"
    overlay = tmp_path / "ovl.png"
    _make_png(source)
    _make_png(overlay)

    monkeypatch.setattr(
        poc,
        "upload_image_for_clean_repair",
        lambda **kw: f"https://cdn.invalid/{kw['image_path'].name}",
    )

    def fake_submit(**kwargs):
        return {"task_id": "task-123"}

    poll_calls = []
    fetch_calls = []

    def fake_poll(*, base_url, api_key, timeout, session, task_id):
        poll_calls.append(task_id)
        return {"status": "succeeded"}

    def fake_fetch(*, base_url, api_key, timeout, session, task_id):
        fetch_calls.append(task_id)
        return {"result_url": "https://cdn.invalid/clean.png"}

    def fake_download(fetch_data, *, output_dir, session):
        out = Path(output_dir) / "clean.png"
        _make_png(out)
        return str(out)

    monkeypatch.setattr(poc, "submit_generation_for_clean_repair", fake_submit)
    monkeypatch.setattr(poc.toapis, "poll_task_status", fake_poll)
    monkeypatch.setattr(poc.toapis, "fetch_task_result", fake_fetch)
    monkeypatch.setattr(poc.toapis, "download_image", fake_download)

    result = poc.run(
        source_image=source,
        output_dir=tmp_path / "out",
        mask_overlay=overlay,
        mode="source_plus_overlay",
        provider_base_url="https://example.invalid",
        api_key="k",
        model="gpt-image-2",
        timeout=5.0,
        prompt_file=None,
    )

    assert result["result_protocol"] == poc.ASYNC_TASK_PROTOCOL
    assert result["task_id"] == "task-123"
    assert poll_calls == ["task-123"]
    assert fetch_calls == ["task-123"]
    assert result["output_matches_source_size"] is True


def test_detect_protocol_sync_vs_async():
    assert poc.detect_result_protocol({"data": [{"b64_json": "xx"}]}) == poc.SYNC_RESULT_PROTOCOL
    assert poc.detect_result_protocol({"data": [{"url": "https://x/y.png"}]}) == poc.SYNC_RESULT_PROTOCOL
    assert poc.detect_result_protocol({"task_id": "t1"}) == poc.ASYNC_TASK_PROTOCOL
    assert poc.detect_result_protocol({"id": "t2"}) == poc.ASYNC_TASK_PROTOCOL
    with pytest.raises(ValueError):
        poc.detect_result_protocol({"unexpected": True})


def _tiny_b64() -> str:
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (64, 32), (10, 20, 30, 255)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
