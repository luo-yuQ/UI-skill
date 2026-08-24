from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ui_text_extractor as ui_text_module  # noqa: E402
from ui_text_extractor import UITextExtractor  # noqa: E402


def _write_source(path: Path) -> None:
    image = np.full((100, 180, 3), 32, dtype=np.uint8)
    cv2.rectangle(image, (18, 18), (28, 40), (245, 245, 245), cv2.FILLED)
    cv2.rectangle(image, (36, 18), (46, 40), (245, 245, 245), cv2.FILLED)
    success, encoded = cv2.imencode(".png", image)
    assert success
    encoded.tofile(str(path))


def test_extract_filters_candidates_and_writes_outputs(tmp_path: Path) -> None:
    source = tmp_path / "界面.png"
    output_json = tmp_path / "result" / "texts.json"
    debug_image = tmp_path / "result" / "debug.png"
    _write_source(source)

    ocr_rows = [
        [[[10, 10], [60, 10], [60, 50], [10, 50]], "开始", 0.96],
        [[[70, 10], [90, 10], [90, 30], [70, 30]], "X", 0.80],
        [[[100, 10], [105, 10], [105, 40], [100, 40]], "装饰", 0.60],
        [[[110, 10], [150, 10], [150, 30], [110, 30]], "忽略", 0.20],
    ]
    extractor = UITextExtractor(ocr_engine=lambda _image: (ocr_rows, [0.01]))

    layers = extractor.extract(str(source), str(output_json), str(debug_image))

    assert len(layers) == 1
    layer = layers[0]
    assert layer["id"] == "text_000"
    assert layer["text"] == "开始"
    assert layer["rect"] == {"x": 10, "y": 10, "width": 50, "height": 40}
    assert layer["style"]["fontFamily"] == "Microsoft YaHei"
    assert layer["style"]["fontSize"] == 33
    assert layer["style"]["color"] == "#F0F0F0"
    assert layer["style"]["strokeColor"] == "#1E2322"
    assert json.loads(output_json.read_text(encoding="utf-8")) == layers
    assert cv2.imdecode(np.fromfile(str(debug_image), dtype=np.uint8), cv2.IMREAD_COLOR) is not None


def test_empty_ocr_still_writes_empty_json_and_debug_image(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output_json = tmp_path / "texts.json"
    debug_image = tmp_path / "debug.png"
    _write_source(source)
    extractor = UITextExtractor(ocr_engine=lambda _image: (None, None))

    assert extractor.extract(str(source), str(output_json), str(debug_image)) == []
    assert json.loads(output_json.read_text(encoding="utf-8")) == []
    assert debug_image.is_file()


def test_bbox_is_clamped_and_single_chinese_uses_height_factor(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _write_source(source)
    ocr_rows = [
        [[[-5, -4], [25, -4], [25, 21], [-5, 21]], "中", 0.99],
    ]
    extractor = UITextExtractor(ocr_engine=lambda _image: (ocr_rows, None))

    layers = extractor.extract(
        str(source),
        str(tmp_path / "texts.json"),
        str(tmp_path / "debug.png"),
    )

    assert layers[0]["rect"] == {"x": 0, "y": 0, "width": 25, "height": 21}
    assert layers[0]["style"]["fontSize"] == 18


def test_missing_image_raises_even_with_injected_ocr(tmp_path: Path) -> None:
    extractor = UITextExtractor(ocr_engine=lambda _image: ([], None))

    try:
        extractor.extract(
            str(tmp_path / "missing.png"),
            str(tmp_path / "texts.json"),
            str(tmp_path / "debug.png"),
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


def test_cjk_family_detection_covers_kana_and_hangul() -> None:
    assert UITextExtractor._is_cjk("カ")
    assert UITextExtractor._is_cjk("한")
    assert not UITextExtractor._is_chinese_ideograph("カ")
    assert UITextExtractor._is_chinese_ideograph("中")


class _FakeCLIExtractor:
    def __init__(self, failing_names: set[str] | None = None) -> None:
        self.calls: list[tuple[Path, Path, Path]] = []
        self.failing_names = failing_names or set()

    def extract(
        self,
        image_path: str,
        output_json_path: str,
        debug_vis_path: str,
    ) -> list[dict[str, object]]:
        paths = (Path(image_path), Path(output_json_path), Path(debug_vis_path))
        self.calls.append(paths)
        if paths[0].name in self.failing_names:
            raise ValueError("synthetic per-image failure")
        return [{"id": "text_000"}]


def test_cli_single_image_uses_automatic_output_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "my_bag.png"
    source.write_bytes(b"placeholder")
    output_dir = tmp_path / "outputs"
    fake = _FakeCLIExtractor()
    monkeypatch.setattr(ui_text_module, "UITextExtractor", lambda: fake)

    result = ui_text_module.main(
        [str(source), "--output-dir", str(output_dir)]
    )

    assert result == 0
    assert fake.calls == [
        (
            source,
            output_dir / "my_bag_texts.json",
            output_dir / "my_bag_debug.png",
        )
    ]
    assert output_dir.is_dir()


def test_cli_single_image_explicit_outputs_take_priority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "screen.webp"
    source.write_bytes(b"placeholder")
    explicit_json = tmp_path / "custom" / "result.json"
    explicit_vis = tmp_path / "custom" / "result.jpg"
    fake = _FakeCLIExtractor()
    monkeypatch.setattr(ui_text_module, "UITextExtractor", lambda: fake)

    result = ui_text_module.main(
        [
            str(source),
            "--output-dir",
            str(tmp_path / "unused"),
            "--output-json",
            str(explicit_json),
            "--output-vis",
            str(explicit_vis),
        ]
    )

    assert result == 0
    assert fake.calls == [(source, explicit_json, explicit_vis)]


def test_cli_directory_continues_after_image_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    failed_image = input_dir / "a_bad.JPG"
    good_image = input_dir / "b_good.jpeg"
    failed_image.write_bytes(b"placeholder")
    good_image.write_bytes(b"placeholder")
    (input_dir / "ignored.txt").write_text("not an image", encoding="utf-8")
    output_dir = tmp_path / "outputs"
    fake = _FakeCLIExtractor(failing_names={failed_image.name})
    monkeypatch.setattr(ui_text_module, "UITextExtractor", lambda: fake)

    result = ui_text_module.main(
        [str(input_dir), "--output-dir", str(output_dir)]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert [call[0] for call in fake.calls] == [failed_image, good_image]
    assert fake.calls[1][1:] == (
        output_dir / "b_good_texts.json",
        output_dir / "b_good_debug.png",
    )
    assert "synthetic per-image failure" in captured.err
    assert "Summary: 1 succeeded, 1 failed, 2 total." in captured.out


def test_cli_rejects_explicit_output_for_directory(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "screen.png").write_bytes(b"placeholder")
    fake = _FakeCLIExtractor()
    monkeypatch.setattr(ui_text_module, "UITextExtractor", lambda: fake)

    result = ui_text_module.main(
        [str(input_dir), "--output-json", str(tmp_path / "result.json")]
    )

    assert result == 2
    assert not fake.calls
    assert "single image" in capsys.readouterr().err
