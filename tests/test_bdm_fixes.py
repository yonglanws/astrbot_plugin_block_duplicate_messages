from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _isolated_managers():
    astrbot = ModuleType("astrbot")
    api = ModuleType("astrbot.api")
    event_mod = ModuleType("astrbot.api.event")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *_a, **_kw: None

    api.logger = _Logger()
    event_mod.AstrMessageEvent = object

    pkg = ModuleType("astrbot_plugin_block_duplicate_messages")
    pkg.__path__ = [str(PLUGIN_ROOT)]
    stubs = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event_mod,
        "astrbot_plugin_block_duplicate_messages": pkg,
    }
    with patch.dict(sys.modules, stubs):
        for mod_name in ("constants", "models", "utils", "managers"):
            spec = importlib.util.spec_from_file_location(
                f"astrbot_plugin_block_duplicate_messages.{mod_name}",
                PLUGIN_ROOT / f"{mod_name}.py",
            )
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"astrbot_plugin_block_duplicate_messages.{mod_name}"] = mod
            spec.loader.exec_module(mod)
        yield (
            sys.modules["astrbot_plugin_block_duplicate_messages.models"],
            sys.modules["astrbot_plugin_block_duplicate_messages.managers"],
        )


def test_non_command_unwoken_skips_decision_delay_sleep(monkeypatch):
    with _isolated_managers() as (models_mod, managers_mod):
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float):
            sleep_calls.append(delay)

        monkeypatch.setattr(managers_mod.asyncio, "sleep", fake_sleep)

        cfg = models_mod.PluginConfig(
            cross_platform_dedup=True,
            cross_platform_platforms="qq_official,aiocqhttp",
            platform_priority="aiocqhttp,qq_official",
            decision_delay_ms=1000,
        )

        class FakePlatform:
            def __init__(self, name: str):
                self._name = name

            def meta(self):
                return type("Meta", (), {"name": self._name})()

        fake_ctx = type(
            "FakeCtx",
            (),
            {
                "platform_manager": type(
                    "PM", (), {"platform_insts": [FakePlatform("aiocqhttp"), FakePlatform("qq_official")]}
                )()
            },
        )()
        flt = managers_mod.DuplicateMessageFilter(cfg, context=fake_ctx)

        # 非指令且 woken=False：立即放行且不 sleep 1000ms
        allowed, _ = asyncio.run(
            flt.check(
                models_mod.MessageInfo(
                    platform_name="qq_official",
                    platform_id="qqofficial",
                    self_id="bot1",
                    group_id="G1",
                    group_name="测试群",
                    sender_id="U1",
                    message_str="哈哈哈哈",
                    command="hash1",
                    woken=False,
                )
            )
        )
        assert allowed is True
        assert sleep_calls == []

        # woken=True 或 /指令 仍应走决策延迟
        allowed2, _ = asyncio.run(
            flt.check(
                models_mod.MessageInfo(
                    platform_name="qq_official",
                    platform_id="qqofficial",
                    self_id="bot1",
                    group_id="G1",
                    group_name="测试群",
                    sender_id="U2",
                    message_str="/help",
                    command="help",
                    woken=False,
                )
            )
        )
        assert allowed2 is True
        assert sleep_calls == [1.0]


def test_drifted_files_use_lf_line_endings():
    for name in ("main.py", "metadata.yaml", "README.md", "LICENSE", ".gitignore"):
        content = (PLUGIN_ROOT / name).read_bytes()
        assert b"\r\n" not in content, f"{name} still contains CRLF"
