from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_recursive_runtime  # noqa: E402
from vlm_client import (  # noqa: E402
    ResponsesAPIVLMClient,
    VLMClientConfig,
    VLMConfigurationError,
    create_configured_vlm_client,
)


class VLMClientConfigTests(unittest.TestCase):
    def test_environment_configuration_is_loaded_without_secret_repr(self):
        secret = "do-not-disclose"
        with patch.dict(
            os.environ,
            {
                "STAGE2A_VLM_BASE_URL": "https://vlm.example.invalid",
                "STAGE2A_VLM_API_KEY": secret,
                "STAGE2A_VLM_MODEL": "env-model",
                "STAGE2A_VLM_TIMEOUT": "12.5",
            },
            clear=True,
        ):
            config = VLMClientConfig.from_env()
        self.assertEqual("env-model", config.model)
        self.assertEqual(12.5, config.timeout)
        self.assertNotIn(secret, repr(config))
        self.assertNotIn(secret, str(config.safe_metadata()))

    def test_model_override_takes_precedence_over_environment(self):
        with patch.dict(
            os.environ,
            {
                "STAGE2A_VLM_BASE_URL": "https://vlm.example.invalid",
                "STAGE2A_VLM_API_KEY": "secret",
                "STAGE2A_VLM_MODEL": "env-model",
            },
            clear=True,
        ):
            config = VLMClientConfig.from_env(model_override="cli-model")
        self.assertEqual("cli-model", config.model)

    def test_model_override_allows_missing_environment_model(self):
        with patch.dict(
            os.environ,
            {
                "STAGE2A_VLM_BASE_URL": "https://vlm.example.invalid",
                "STAGE2A_VLM_API_KEY": "secret",
            },
            clear=True,
        ):
            config = VLMClientConfig.from_env(model_override="cli-model")
        self.assertEqual("cli-model", config.model)

    def test_missing_model_without_override_fails_closed(self):
        with patch.dict(
            os.environ,
            {
                "STAGE2A_VLM_BASE_URL": "https://vlm.example.invalid",
                "STAGE2A_VLM_API_KEY": "secret",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                VLMConfigurationError, "STAGE2A_VLM_MODEL"
            ):
                VLMClientConfig.from_env()

    def test_missing_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                VLMConfigurationError, "STAGE2A_VLM_BASE_URL"
            ):
                VLMClientConfig.from_env()

    def test_configured_client_is_responses_api_client_without_secret_repr(self):
        secret = "do-not-disclose"
        config = VLMClientConfig(
            base_url="https://vlm.example.invalid",
            api_key=secret,
            model="vision-model",
        )
        client = create_configured_vlm_client(config)
        self.assertIsInstance(client, ResponsesAPIVLMClient)
        self.assertNotIn(secret, repr(client))

    def test_cli_production_failure_never_falls_back_to_interactive_or_fake(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(stderr):
                code = run_recursive_runtime.main(
                    [
                        "--run-dir",
                        str(Path(directory) / "run"),
                        "--adapter",
                        "production",
                    ]
                )
        self.assertEqual(1, code)
        self.assertIn("production VLM configuration is missing", stderr.getvalue())
        self.assertNotIn("WAITING_FOR_ADAPTER", stderr.getvalue())

    def test_cli_model_reaches_production_vlm_client(self):
        with patch.dict(
            os.environ,
            {
                "STAGE2A_VLM_BASE_URL": "https://vlm.example.invalid",
                "STAGE2A_VLM_API_KEY": "secret",
                "STAGE2A_VLM_MODEL": "env-model",
            },
            clear=True,
        ):
            args = run_recursive_runtime.build_parser().parse_args(
                [
                    "--run-dir",
                    "unused",
                    "--adapter",
                    "production",
                    "--model",
                    "cli-model",
                ]
            )
            adapters = run_recursive_runtime.build_adapters(
                args.adapter,
                args.run_dir,
                model=args.model,
            )
        self.assertEqual("cli-model", adapters.router.vlm_client.config.model)


if __name__ == "__main__":
    unittest.main()
