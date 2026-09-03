from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ui_image_clean_repair_poc as poc  # noqa: E402


def _write_image(path: Path, size: tuple[int, int] = (1600, 900)) -> None:
    Image.new("RGB", size, "navy").save(path)


def _write_alpha_hole(path: Path, size: tuple[int, int] = (1024, 1536)) -> None:
    image = Image.new("RGBA", size, (200, 200, 200, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 60, 60], fill=(0, 0, 0, 0))
    image.save(path, format="PNG")


def _write_prompt(path: Path) -> None:
    path.write_text("Remove the text and repair the surface.", encoding="utf-8")


def _args(tmp_path: Path, source: Path, overlay: Path | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        image=str(source),
        output_dir=str(tmp_path / "output"),
        mask_overlay=str(overlay) if overlay is not None else None,
        input_mode=None,
        model="gpt-image-2",
        prompt_file=None,
        provider_size=None,
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
    prompt_file = tmp_path / "prompt.txt"
    _write_prompt(prompt_file)
    captured: dict[str, Any] = {}
    _mock_success(monkeypatch, captured)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")
    args = _args(tmp_path, source)
    args.prompt_file = prompt_file

    code, result = poc.run(args, session=object())

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
    prompt_file = tmp_path / "prompt.txt"
    _write_prompt(prompt_file)
    monkeypatch.setenv("TOAPIS_API_KEY", secret)

    def failing_upload(*_: Any, **__: Any) -> str:
        raise RuntimeError(f"simulated provider failure containing {secret}")

    monkeypatch.setattr(poc.toapis, "upload_image", failing_upload)
    args = _args(tmp_path, source)
    args.prompt_file = prompt_file

    code, result = poc.run(args, session=object())
    print(json.dumps(result, ensure_ascii=False))
    stdout = capsys.readouterr().out
    result_text = (Path(args.output_dir) / "result.json").read_text(encoding="utf-8")

    assert code != 0
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert secret not in result_text
    assert secret not in stdout
    assert "[REDACTED]" in result["error_message"]


# ---------------------------------------------------------------------------
# alpha_hole_only experiment mode
# ---------------------------------------------------------------------------


def test_alpha_hole_input_check_passes_for_rgba_with_hole(tmp_path: Path) -> None:
    alpha_hole = tmp_path / "alpha-hole.png"
    _write_alpha_hole(alpha_hole)

    assert poc.inspect_alpha_hole(alpha_hole) == (1024, 1536)


def test_alpha_hole_rejects_image_without_alpha_channel(tmp_path: Path) -> None:
    rgb_png = tmp_path / "rgb.png"
    Image.new("RGB", (64, 64), "navy").save(rgb_png)
    jpeg = tmp_path / "photo.jpg"
    Image.new("RGB", (64, 64), "navy").save(jpeg)

    for path in (rgb_png, jpeg):
        with pytest.raises(
            poc.CleanRepairError,
            match="alpha_hole_only requires an image with an alpha channel",
        ):
            poc.inspect_alpha_hole(path)


def test_alpha_hole_rejects_opaque_rgba(tmp_path: Path) -> None:
    opaque = tmp_path / "opaque.png"
    Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(opaque)

    with pytest.raises(
        poc.CleanRepairError,
        match="alpha_hole_only input contains no fully transparent pixels",
    ):
        poc.inspect_alpha_hole(opaque)


def test_alpha_hole_rejects_fully_transparent_rgba(tmp_path: Path) -> None:
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(transparent)

    with pytest.raises(
        poc.CleanRepairError,
        match="alpha_hole_only input is fully transparent",
    ):
        poc.inspect_alpha_hole(transparent)


def test_alpha_hole_only_with_mask_overlay_fails_fast(
    monkeypatch: Any, tmp_path: Path
) -> None:
    alpha_hole = tmp_path / "alpha-hole.png"
    _write_alpha_hole(alpha_hole)
    overlay = tmp_path / "overlay.png"
    _write_image(overlay)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")
    args = _args(tmp_path, alpha_hole, overlay)
    args.input_mode = "alpha_hole_only"

    code, result = poc.run(args, session=object())

    assert code == 2
    assert result["mode"] == "alpha_hole_only"
    assert "alpha_hole_only must not be combined with --mask-overlay" in result["error_message"]


def test_source_plus_overlay_without_mask_overlay_fails_fast(
    monkeypatch: Any, tmp_path: Path
) -> None:
    source = tmp_path / "source.png"
    _write_image(source)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")
    args = _args(tmp_path, source)
    args.input_mode = "source_plus_overlay"

    code, result = poc.run(args, session=object())

    assert code == 2
    assert "source_plus_overlay requires --mask-overlay" in result["error_message"]


def _mock_alpha_success(monkeypatch: Any, captured: dict[str, Any]) -> None:
    def upload_image(path: Path, **_: Any) -> str:
        return f"https://cdn.example/{path.name}"

    def submit_generation(payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        captured["payload"] = payload
        return {"task_id": "task-alpha-1"}

    def download_image(_: str, output_path: Path, **__: Any) -> None:
        # Simulate provider storage that keeps the PNG alpha channel intact.
        _write_alpha_hole(output_path)

    monkeypatch.setattr(poc.toapis, "upload_image", upload_image)
    monkeypatch.setattr(poc.toapis, "submit_generation", submit_generation)
    monkeypatch.setattr(poc.toapis, "poll_task_status", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        poc.toapis,
        "fetch_task_result",
        lambda *args, **kwargs: ({}, "https://cdn.example/clean.png"),
    )
    monkeypatch.setattr(poc.toapis, "download_image", download_image)


def test_alpha_hole_only_run_uses_single_image_and_records_probe(
    monkeypatch: Any, tmp_path: Path
) -> None:
    alpha_hole = tmp_path / "alpha-hole.png"
    _write_alpha_hole(alpha_hole)
    captured: dict[str, Any] = {}
    _mock_alpha_success(monkeypatch, captured)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")
    args = _args(tmp_path, alpha_hole)
    args.input_mode = "alpha_hole_only"

    code, result = poc.run(args, session=object())

    assert code == 0
    assert result["mode"] == "alpha_hole_only"
    assert captured["payload"]["images"] == ["https://cdn.example/alpha-hole.png"]
    assert len(captured["payload"]["images"]) == 1
    assert result["alpha_probe"] == {
        "local_alpha_mode": "RGBA",
        "local_alpha_extrema": [0, 255],
        "uploaded_alpha_mode": "RGBA",
        "uploaded_alpha_extrema": [0, 255],
        "uploaded_alpha_preserved": True,
    }


def test_alpha_hole_only_default_prompt(monkeypatch: Any, tmp_path: Path) -> None:
    alpha_hole = tmp_path / "alpha-hole.png"
    _write_alpha_hole(alpha_hole)
    captured: dict[str, Any] = {}
    _mock_alpha_success(monkeypatch, captured)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")
    args = _args(tmp_path, alpha_hole)
    args.input_mode = "alpha_hole_only"

    code, result = poc.run(args, session=object())

    assert code == 0
    assert captured["payload"]["prompt"] == poc.ALPHA_HOLE_ONLY_PROMPT


def test_alpha_hole_only_probe_records_error_without_breaking_flow(
    monkeypatch: Any, tmp_path: Path
) -> None:
    alpha_hole = tmp_path / "alpha-hole.png"
    _write_alpha_hole(alpha_hole)
    captured: dict[str, Any] = {}
    _mock_alpha_success(monkeypatch, captured)

    def failing_probe_download(url: str, output_path: Path, **__: Any) -> None:
        if output_path.name.startswith(".alpha-probe."):
            raise RuntimeError(f"probe download failed for {url}")
        _write_alpha_hole(output_path)

    monkeypatch.setattr(poc.toapis, "download_image", failing_probe_download)
    monkeypatch.setenv("TOAPIS_API_KEY", "unit-test-secret")
    args = _args(tmp_path, alpha_hole)
    args.input_mode = "alpha_hole_only"

    code, result = poc.run(args, session=object())

    # Probe failure must not change the provider request outcome.
    assert code == 0
    assert captured["payload"]["images"] == ["https://cdn.example/alpha-hole.png"]
    assert "error" in result["alpha_probe"]
    assert "uploaded_alpha_preserved" not in result["alpha_probe"]
