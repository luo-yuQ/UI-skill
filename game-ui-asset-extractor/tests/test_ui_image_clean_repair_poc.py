from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ui_image_clean_repair_poc as poc  # noqa: E402


def _write_image(path: Path, size: tuple[int, int] = (1600, 900)) -> None:
    Image.new("RGB", size, "navy").save(path)


def _args(tmp_path: Path, source: Path, overlay: Path | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        image=str(source),
        output_dir=str(tmp_path / "output"),
        mask_overlay=str(overlay) if overlay is not None else None,
        model="gpt-image-2",
        prompt_file=None,
        upload_timeout=120.0,
        request_timeout=120.0,
        download_timeout=180.0,
        poll_interval=3.0,
        max_wait=300.0,
    )


def _mock_success(monkeypatch: Any, captured: dict[str, Any]) -> None:
    def upload_image(path: Path, **_: Any) -> str:
        return f"https://cdn.example/{path.name}"

    def submit_generation(payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        captured["payload"] = payload
        return {"task_id": "task-clean-1"}

    def download_image(_: str, output_path: Path, **__: Any) -> None:
        Image.new("RGB", (1536, 1024), "teal").save(output_path, format="PNG")

    monkeypatch.setattr(poc.toapis, "upload_image", upload_image)
    monkeypatch.setattr(poc.toapis, "submit_generation", submit_generation)
    monkeypatch.setattr(poc.toapis, "poll_task_status", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        poc.toapis,
        "fetch_task_result",
        lambda *args, **kwargs: ({}, "https://cdn.example/clean.png"),
    )
    monkeypatch.setattr(poc.toapis, "download_image", download_image)


def test_source_only_payload_uses_one_ordered_image(monkeypatch: Any, tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _write_image(source)
    captured: dict[str, Any] = {}
    _mock_success(monkeypatch, captured)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")

    code, result = poc.run(_args(tmp_path, source), session=object())

    assert code == 0
    assert result["mode"] == "source_only"
    assert captured["payload"]["images"] == ["https://cdn.example/source.png"]
    assert captured["payload"]["type"] == "image"
    assert captured["payload"]["model"] == "gpt-image-2"
    assert captured["payload"]["size"] == "1536x1024"


def test_overlay_payload_preserves_source_then_overlay_order(
    monkeypatch: Any, tmp_path: Path
) -> None:
    source = tmp_path / "source.png"
    overlay = tmp_path / "repair-mask-overlay.png"
    _write_image(source)
    _write_image(overlay)
    captured: dict[str, Any] = {}
    _mock_success(monkeypatch, captured)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")

    code, result = poc.run(_args(tmp_path, source, overlay), session=object())

    assert code == 0
    assert result["mode"] == "source_plus_overlay"
    assert captured["payload"]["images"] == [
        "https://cdn.example/source.png",
        "https://cdn.example/repair-mask-overlay.png",
    ]


def test_payload_does_not_use_unverified_edit_protocol_fields() -> None:
    payload = poc.build_generation_payload(
        model="gpt-image-2",
        prompt="repair",
        image_urls=["https://cdn.example/source.png", "https://cdn.example/overlay.png"],
        provider_size="1024x1536",
    )

    assert "mask" not in payload
    assert "reference_images" not in payload
    assert set(payload) == {
        "model",
        "type",
        "images",
        "prompt",
        "size",
        "n",
        "response_format",
    }
    assert "/images/edits" not in json.dumps(payload)


def test_api_key_is_redacted_from_result_file_and_stdout(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    secret = "top-secret-clean-repair-key"
    source = tmp_path / "source.png"
    _write_image(source)
    monkeypatch.setenv("TOAPIS_API_KEY", secret)

    def failing_upload(*_: Any, **__: Any) -> str:
        raise RuntimeError(f"simulated provider failure containing {secret}")

    monkeypatch.setattr(poc.toapis, "upload_image", failing_upload)
    args = _args(tmp_path, source)

    code, result = poc.run(args, session=object())
    print(json.dumps(result, ensure_ascii=False))
    stdout = capsys.readouterr().out
    result_text = (Path(args.output_dir) / "result.json").read_text(encoding="utf-8")

    assert code != 0
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert secret not in result_text
    assert secret not in stdout
    assert "[REDACTED]" in result["error_message"]
