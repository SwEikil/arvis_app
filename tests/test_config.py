from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

import config


class ConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        importlib.reload(config)

    def test_config_loads_without_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            reloaded = importlib.reload(config)

        self.assertEqual(reloaded.OLLAMA_HOST, "http://127.0.0.1:11434")
        self.assertEqual(reloaded.ARVIS_MODEL, "arvis")
        self.assertIsNone(reloaded.get_minecraft_server_config())

    def test_minecraft_server_config_created_from_env_when_enabled(self) -> None:
        env = {
            "MINECRAFT_SERVER_ENABLED": "true",
            "MINECRAFT_SERVER_KEY": "default",
            "MINECRAFT_SERVER_NAME": "Test Minecraft Server",
            "MINECRAFT_SERVER_CWD": "/tmp/arvis-test-server",
            "MINECRAFT_SERVER_COMMAND": "bash ./start-server.sh",
        }
        with patch.dict(os.environ, env, clear=True):
            reloaded = importlib.reload(config)
            server = reloaded.get_minecraft_server_config()

        self.assertIsNotNone(server)
        assert server is not None
        self.assertEqual(server.key, "default")
        self.assertEqual(server.name, "Test Minecraft Server")
        self.assertEqual(str(server.cwd), "/tmp/arvis-test-server")
        self.assertEqual(server.command, ["bash", "./start-server.sh"])
        self.assertEqual(server.tmux_session, "arvis_minecraft_default")

    def test_app_command_from_env_is_shlex_split(self) -> None:
        with patch.dict(os.environ, {"SPOTIFY_COMMAND": "flatpak run com.spotify.Client"}, clear=True):
            reloaded = importlib.reload(config)

        self.assertEqual(reloaded.APP_COMMANDS["spotify"][0], ["flatpak", "run", "com.spotify.Client"])

    def test_website_command_from_env_is_shlex_split(self) -> None:
        with patch.dict(os.environ, {"YOUTUBE_COMMAND": "xdg-open https://www.youtube.com/"}, clear=True):
            reloaded = importlib.reload(config)

        self.assertEqual(reloaded.APP_COMMANDS["youtube"][0], ["xdg-open", "https://www.youtube.com/"])

    def test_empty_app_command_uses_fallback(self) -> None:
        with patch.dict(os.environ, {"BRAVE_COMMAND": ""}, clear=True):
            reloaded = importlib.reload(config)

        self.assertEqual(reloaded.APP_COMMANDS["brave"], [["brave-browser"], ["brave"]])


if __name__ == "__main__":
    unittest.main()
