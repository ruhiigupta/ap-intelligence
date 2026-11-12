"""
tests/test_environment_and_vision.py — Test environment loading and Cloud Vision integration with fallback.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

import app.config as config
from app.services.reader import read_page


def make_dummy_image(text="TEST INVOICE") -> Path:
    tmp_path = Path("backend/storage/test_env_vision.png")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (300, 100), color="white")
    img.save(tmp_path, format="PNG")
    return tmp_path


def test_dotenv_loading_resolution():
    """Verify environment variable bindings are correctly configured."""
    assert hasattr(config, "GEMINI_API_KEY")
    assert hasattr(config, "OPENAI_API_KEY")
    assert hasattr(config, "GEMINI_MODEL")
    assert config._backend_env.name == ".env"
    assert config._root_env.name == ".env"


def test_cloud_vision_with_api_key():
    """Verify Gemini client initialization and structured JSON extraction."""
    img_path = make_dummy_image()

    fake_gemini_json = (
        '{"page_number": 1, '
        '"tokens": [{"text": "Zendesk, Inc.", "bbox": [50.0, 50.0, 200.0, 80.0]}], '
        '"tables": [], '
        '"raw_key_values": {"Invoice Number": "INV-GEMINI-01", "Vendor": "Zendesk, Inc.", "Total": "28.00"}}'
    )

    mock_response = MagicMock()
    mock_response.text = fake_gemini_json

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyFakeKeyForTesting123456"}), \
         patch("google.genai.Client", return_value=mock_client) as mock_genai_init:

        result, engine, version, exec_time = read_page(img_path, "image/png", 1)

        assert mock_genai_init.called
        assert mock_client.models.generate_content.called
        assert result.raw_key_values.get("Invoice Number") == "INV-GEMINI-01"
        assert result.raw_key_values.get("Vendor") == "Zendesk, Inc."


def test_fallback_when_api_key_missing():
    """Verify deterministic fallback to local OCR when no API keys are present."""
    img_path = make_dummy_image()

    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}), \
         patch("app.services.reader.GEMINI_API_KEY", ""), \
         patch("app.services.reader.OPENAI_API_KEY", ""):
        result, engine, version, exec_time = read_page(img_path, "image/png", 1)

        assert result is not None
        assert engine in ("rapidocr-onnx", "native-vector-reader")
        assert result.page_number == 1


def test_fallback_when_cloud_call_fails():
    """Verify graceful degradation to local OCR if cloud vision call throws."""
    img_path = make_dummy_image()

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("Simulated Gemini QuotaExceeded")

    with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyFakeKeyForTesting123456"}), \
         patch("google.genai.Client", return_value=mock_client):

        result, engine, version, exec_time = read_page(img_path, "image/png", 1)
        assert result is not None
        assert engine in ("rapidocr-onnx", "native-vector-reader")
