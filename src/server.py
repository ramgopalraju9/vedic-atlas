"""Composition root — wires every port to a concrete adapter and boots FastAPI.

Donor: veda/app.py, read in full (489 real lines). This is the direct
analogue of `_bootstrap_agents()` + `lifespan()` + route registration, but
built from scratch against the ported `domain`/`service`/`tpa`/`controller`
tree rather than adapted line-by-line — the donor wired Teams, vision, the
Claude/Copilot CLI, and a code-runner agent, none of which exist here.

Deliberately NOT wired this batch (see docs/migration/MIGRATION_LEDGER.md's
Batch 11 entry for the full reasoning on each):
  - The always-on ambient voice loop (`service/voice/voice_session.py` was
    never built in any batch — `AmbientLoop`/wake-word/STT/TTS adapters
    exist in `tpa/` but have no orchestrator wired to them yet).
  - `ResponderAgent` has no skill-calling loop — the donor's tool-calling
    relied on the Claude/Copilot CLI's native tool-use protocol, which a
    raw Ollama/llama.cpp `complete()` call doesn't provide equivalently.
    `SkillRunner` is still constructed and available on `app.state` for a
    future tool-calling redesign; `SystemAgent`'s bespoke single-action
    JSON protocol is the only skill-invocation path wired today.
"""

from __future__ import annotations

import asyncio
import os
import platform
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from controller.middleware.request_context import request_context
from controller.routes import (
    admin, ambient, approval, chat, config as config_route, governance as governance_route,
    health, knowledge, lookup, memory as memory_route, persona, privacy, speakers, stream, system,
    tasks as tasks_route, trace as trace_route, voice,
)
from core.config import ensure_dirs, load_full_config
from core.constants import PROJECT_NAME, VERSION
from core.env import load_env
from core.logging_config import configure_logging, logger
from domain.policies.token_budget_policy import PromptBudgets, estimate_tokens
from domain.events.ambient_event import AmbientEvent
from domain.events.event_kind import EventKind
from domain.value_objects.urgency import Urgency
from exceptions.handlers import register_exception_handlers
from schemas.config_schemas import AppConfig
from service.agent.registry import AgentRegistry
from service.agent.llm_router import LlmRouter
from service.agent.tool_agent import ToolAgent
from service.agent.tool_turn_runner import ToolTurnRunner
from service.agent.tool_use_guard import ToolUseGuard
from service.prompting.prompt_composer import PromptComposer
from service.agent.responder import ResponderAgent
from service.agent.supervisor import SupervisorAgent
from service.agent.system import SystemAgent
from service.approval.approval_broker import ApprovalBroker
from service.conversation.conversation_manager import ConversationManager
from service.conversation.summariser import ConversationSummariser
from service.governance.factory import build_governance
from service.guardrails.audit_log import AuditLogger
from service.guardrails.permissions import PermissionManager
from service.guardrails.rate_limit import RateLimiter
from service.guardrails.validators import InputValidator, OutputValidator
from service.hooks.dispatcher import (
    create_audit_hook, create_input_validator_hook, create_output_validator_hook,
    create_permission_check_hook, create_rate_limit_hook,
)
from service.hooks.registry import HookRegistry
from service.lookup.health_service import HealthProbe, LookupHealthService
from service.lookup.lookup_service import LookupService
from service.lookup.ttl_cache import TtlCache
from service.lookup.registry import FactProviderRegistry
from service.memory.knowledge_base import KnowledgeBase
from service.privacy.capture_gate import CaptureGate
from service.sensing.event_bus import EventBus
from service.skills.builtin.file_ops import FileOpsSkill
from service.skills.builtin.terminal import TerminalSkill
from service.skills.registry import SkillRegistry
from service.skills.skill_runner import SkillRunner
from tpa.filestore.json_knowledge_store import JsonKnowledgeStore
from tpa.governance.sqlite_audit_sink import NullAuditSink, SqliteAuditSink
from tpa.inference.factory import build_inference_client
from service.inference.graceful_degradation import GracefulDegradation
from service.inference.single_flight import SingleFlight
from tpa.filestore.file_prompt_store import FilePromptStore
from tpa.persistence.repositories.trace_repository import SqliteTraceRepository
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.online.http_client import AllowListedHttpClient
from tpa.online.providers.fx import FxProvider
from tpa.online.providers.geocode import GeocodingProvider
from tpa.online.providers.tavily import TavilyProvider
from tpa.online.providers.weather import WeatherProvider
from tpa.persistence.migrations import init_tables
from tpa.persistence.repositories.agent_memory_repository import AgentMemoryRepository
from tpa.persistence.repositories.conversation_repository import ConversationRepository
from tpa.persistence.session import SessionLocal
from core.enums import HookEvent

_IS_WINDOWS = platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Subsystem builders — each takes the config it needs and returns wired objects.
# ---------------------------------------------------------------------------

def _build_system_control():
    if _IS_WINDOWS:
        from tpa.system.windows_system_adapter import WindowsSystemAdapter
        return WindowsSystemAdapter()
    from tpa.system.linux_system_adapter import LinuxSystemAdapter
    return LinuxSystemAdapter()


def _build_notifier():
    """Best-effort desktop notifier. `None` (no notifications) on any other platform."""
    if not _IS_WINDOWS:
        return None
    from tpa.notifications.desktop_notifier import DesktopNotifier
    return DesktopNotifier()


def _build_mute_switch(cfg: AppConfig):
    """Mute switch selected by privacy.mute_switch; software if it can't start.

    Boots muted unless privacy.start_muted is explicitly false. Any adapter
    that cannot be constructed or started falls back to SoftwareMuteSwitch —
    an input-device failure must never crash the server or leave it unable to
    mute. 'keyboard' reads the global hotkey from config/audio.yaml.
    """
    from tpa.hardware.software_mute_switch import SoftwareMuteSwitch

    start_muted = cfg.privacy.start_muted
    choice = (cfg.privacy.mute_switch or "software").lower()

    if choice == "software":
        return SoftwareMuteSwitch(start_muted=start_muted)

    try:
        if choice == "keyboard":
            from tpa.hardware.keyboard_mute_fallback import KeyboardMuteFallback
            switch = KeyboardMuteFallback(hotkey=cfg.audio.hotkey, start_muted=start_muted)
        elif choice == "gpio":
            from tpa.hardware.gpio_mute_switch import GpioMuteSwitch
            switch = GpioMuteSwitch(pin=cfg.privacy.mute_gpio_pin)
        elif choice == "hid":
            raise RuntimeError("hid mute switch needs vendor_id/product_id (not configured)")
        else:
            logger.warning(f"[privacy] unknown mute_switch '{choice}'; using software")
            return SoftwareMuteSwitch(start_muted=start_muted)

        starter = getattr(switch, "start", None)
        if callable(starter):
            starter()
        logger.info(f"[privacy] mute switch '{choice}' active (source={switch.source_name!r})")
        return switch
    except Exception as e:
        logger.warning(f"[privacy] mute_switch '{choice}' unavailable ({e}); using software")
        return SoftwareMuteSwitch(start_muted=start_muted)


def _build_indicator():
    """Try the BlinkStick USB LED; fall back to the software (log-only) indicator."""
    from tpa.hardware.laptop_indicator import BlinkStickIndicator, SoftwareIndicator
    stick = BlinkStickIndicator()
    try:
        stick.start()
        return stick
    except Exception as e:
        logger.info(f"[privacy] BlinkStick not available ({e}); using SoftwareIndicator")
        return SoftwareIndicator()


def _build_audio_capture(cfg: AppConfig, frame_length: int | None = None):
    """Mic capture adapter, or None when `sounddevice` isn't installed.

    `frame_length` is dictated by the VAD frame size so every captured
    block is exactly one VAD frame — a mismatch silently makes the
    detector reject every frame.
    """
    try:
        from tpa.audio.sounddevice_capture import SAMPLE_RATE, SoundDeviceCapture
    except Exception as e:
        logger.info(f"[privacy] audio capture unavailable ({e}); capture gate will run mic-less")
        return None
    try:
        blocksize = frame_length or int(SAMPLE_RATE * cfg.audio.vad.frame_duration_ms / 1000)
        return SoundDeviceCapture(frame_length=blocksize, device_index=cfg.audio.mic_device_index)
    except Exception as e:
        logger.warning(f"[privacy] failed to build audio capture: {e}")
        return None


def _build_vad(cfg: AppConfig):
    """VAD adapter chosen by config. Falls back to energy if webrtc is absent."""
    from tpa.audio.sounddevice_capture import SAMPLE_RATE

    v = cfg.audio.vad
    if v.engine == "webrtc":
        try:
            from tpa.audio.webrtc_vad import WebRtcVadDetector

            return WebRtcVadDetector(
                aggressiveness=v.aggressiveness,
                frame_duration_ms=v.frame_duration_ms,
                sample_rate=SAMPLE_RATE,
            )
        except Exception as e:
            logger.warning(f"[voice] webrtc VAD unavailable ({e}); falling back to energy VAD")

    try:
        from tpa.audio.energy_vad import EnergyVadDetector

        return EnergyVadDetector(
            frame_duration_ms=v.frame_duration_ms,
            sample_rate=SAMPLE_RATE,
            min_rms=v.min_rms,
            speech_ratio=v.speech_ratio,
            noise_adapt=v.noise_adapt,
        )
    except Exception as e:
        logger.info(f"[voice] VAD unavailable ({e}); continuous listening disabled")
        return None


def _build_stt(cfg: AppConfig):
    """Local STT, or None. Never downloads — the model must be staged."""
    try:
        from core.constants import PROJECT_ROOT
        from tpa.stt.faster_whisper import FasterWhisperProvider

        path = cfg.audio.stt_model_path
        if path:
            resolved = (PROJECT_ROOT / path) if not Path(path).is_absolute() else Path(path)
            if not resolved.exists():
                logger.warning(f"[voice] STT model not found at {resolved}; STT disabled")
                return None
            # faster-whisper accepts a directory in place of a size name.
            return FasterWhisperProvider(model_size=str(resolved))
        return FasterWhisperProvider(model_size=cfg.audio.stt_model)
    except Exception as e:
        logger.info(f"[voice] STT unavailable ({e})")
        return None


def _resolve_tts_engine(cfg: AppConfig) -> str:
    """`auto` = Windows speech (pyttsx3) on Windows, Piper everywhere else."""
    engine = (cfg.audio.tts_engine or "auto").strip().lower()
    if engine == "auto":
        return "pyttsx3" if _IS_WINDOWS else "piper"
    return engine


def _build_tts(cfg: AppConfig):
    """TTS adapter chosen by config, or None.

    Every requirement is checked HERE, at boot, so a missing voice or package is a clear
    startup error ("voice not started - missing: tts") and not a silent device that logs
    "TTS failed" on every reply.
    """
    engine = _resolve_tts_engine(cfg)
    try:
        if engine == "piper":
            model = cfg.audio.tts_model_path
            if not model:
                if _IS_WINDOWS:
                    logger.warning("[voice] tts_engine=piper has no tts_model_path; using Windows speech (pyttsx3) instead")
                    engine = "pyttsx3"
                else:
                    raise ValueError(
                        "Piper needs a voice: copy a Piper voice (.onnx and its .onnx.json) to the device and set "
                        "audio.tts_model_path in config/audio.yaml, e.g. data/models/piper/en_US-lessac-medium.onnx"
                    )
            if engine == "piper":
                import piper  # noqa: F401  (fail now, not on the first reply)
                from core.constants import PROJECT_ROOT
                from tpa.tts.piper import PiperProvider

                path = Path(model)
                logger.info(f"[voice] TTS engine: piper ({path.name})")
                return PiperProvider(model_path=path if path.is_absolute() else PROJECT_ROOT / path)
        if engine == "pyttsx3":
            import pyttsx3  # noqa: F401  (Windows speech; not installed on Linux/Pi)
            from tpa.tts.pyttsx3_provider import Pyttsx3Provider

            logger.info("[voice] TTS engine: pyttsx3 (Windows speech)")
            return Pyttsx3Provider(voice_hint=cfg.audio.tts_voice)
        raise ValueError(f"unknown audio.tts_engine {cfg.audio.tts_engine!r} (use auto | piper | pyttsx3)")
    except ImportError as e:
        hint = (
            "pyttsx3 is Windows-only: set audio.tts_engine: piper (or auto) and give it a voice"
            if engine == "pyttsx3" else "install it with: pip install piper-tts"
        )
        logger.error(f"[voice] TTS unavailable: {e}. {hint}. Voice replies are disabled until this is fixed.")
        return None
    except Exception as e:
        logger.error(f"[voice] TTS unavailable: {e}. Voice replies are disabled until this is fixed.")
        return None


def _build_speaker(cfg: AppConfig):
    try:
        from tpa.audio.playback import SpeakerPlayback

        return SpeakerPlayback(device_index=cfg.audio.speaker_device_index)
    except Exception as e:
        logger.info(f"[voice] speaker unavailable ({e})")
        return None


def _build_wake_word(cfg: AppConfig):
    """Wake trigger selected by audio.wake_engine; None (always-transcribe) on any failure.

    'hotkey' arms a listening window on a global key (does not work headless —
    see docs/07-backlog.md). 'openwakeword' is spoken-wake via a local ONNX
    model, no API key, works headless — the default. 'porcupine' is
    Picovoice's spoken-wake, needs PICOVOICE_ACCESS_KEY + a .ppn keyword file;
    kept available, not installed/used by default (see
    docs/voice/open-source-wake-speaker-design.md). A failure never crashes
    the voice loop — it returns None and the loop transcribes while unmuted,
    exactly as before.
    """
    if not cfg.audio.wake_word_enabled:
        return None
    engine = (cfg.audio.wake_engine or "none").lower()
    if engine == "none":
        return None
    try:
        if engine == "hotkey":
            from tpa.wake_word.hotkey import HotkeyWakeWord
            wake = HotkeyWakeWord(hotkey=cfg.audio.wake_hotkey)
            wake.start()
            logger.info(f"[voice] wake engine 'hotkey' active ({cfg.audio.wake_hotkey})")
            return wake
        if engine == "openwakeword":
            model_path = cfg.audio.wake_oww_model_path
            if not model_path:
                logger.warning("[voice] openwakeword needs audio.wake_oww_model_path; wake disabled")
                return None
            from core.constants import PROJECT_ROOT
            from tpa.wake_word.openwakeword_engine import OpenWakeWordEngine
            resolved = model_path if Path(model_path).is_absolute() else PROJECT_ROOT / model_path
            wake = OpenWakeWordEngine(
                model_name=cfg.audio.wake_oww_model,
                model_path=str(resolved),
                threshold=cfg.audio.wake_threshold,
            )
            logger.info(f"[voice] wake engine 'openwakeword' active ({cfg.audio.wake_oww_model})")
            return wake
        if engine == "porcupine":
            access_key = _picovoice_access_key()
            if not access_key or not cfg.audio.wake_keyword_path:
                logger.warning("[voice] porcupine needs PICOVOICE_ACCESS_KEY + audio.wake_keyword_path; wake disabled")
                return None
            from core.constants import PROJECT_ROOT
            from tpa.wake_word.porcupine import PorcupineWakeWord
            kw = cfg.audio.wake_keyword_path
            resolved = kw if Path(kw).is_absolute() else PROJECT_ROOT / kw
            wake = PorcupineWakeWord(access_key=access_key, keyword_path=str(resolved))
            if getattr(wake, "sample_rate", 16000) not in (0, 16000):
                logger.warning("[voice] porcupine sample rate != 16kHz; wake disabled")
                return None
            starter = getattr(wake, "start", None)
            if callable(starter):
                starter()
            logger.info("[voice] wake engine 'porcupine' active")
            return wake
        logger.warning(f"[voice] unknown wake_engine '{engine}'; wake disabled")
        return None
    except Exception as e:
        logger.warning(f"[voice] wake engine '{engine}' unavailable ({e}); wake disabled")
        return None


def _picovoice_access_key() -> str:
    """One key covers every Picovoice SDK (Porcupine, Eagle, ...). Prefer the
    generic name; fall back to the wake-word-specific one some installs set."""
    import os
    return os.environ.get("PICOVOICE_ACCESS_KEY") or os.environ.get("PORCUPINE_ACCESS_KEY", "")


def _build_speaker_id(cfg: AppConfig):
    """Multi-speaker recognizer, backend selected by audio.speaker_id.backend;
    None (feature off) on any failure.

    'resemblyzer' (default) is local/no-key — see
    docs/voice/open-source-wake-speaker-design.md. 'eagle' is Picovoice,
    kept available, needs PICOVOICE_ACCESS_KEY, not installed/used by
    default. Zero enrolled profiles is not a failure — the recognizer
    still builds and simply scores no one until someone is enrolled via
    /api/speakers.
    """
    scfg = cfg.audio.speaker_id
    if not scfg.enabled:
        return None
    backend = (scfg.backend or "resemblyzer").lower()
    try:
        from core.constants import PROJECT_ROOT
        profiles_dir = PROJECT_ROOT / scfg.profiles_dir
        if backend == "resemblyzer":
            from tpa.speaker.resemblyzer_speaker_recognizer import ResemblyzerSpeakerRecognizer
            recognizer = ResemblyzerSpeakerRecognizer(
                profiles_dir=profiles_dir, score_window_sec=scfg.score_window_sec,
            )
        elif backend == "eagle":
            access_key = _picovoice_access_key()
            if not access_key:
                logger.warning("[voice] speaker ID backend 'eagle' needs PICOVOICE_ACCESS_KEY; disabled")
                return None
            from tpa.speaker.eagle_speaker_recognizer import EagleSpeakerRecognizer
            recognizer = EagleSpeakerRecognizer(access_key=access_key, profiles_dir=profiles_dir)
        else:
            logger.warning(f"[voice] unknown speaker_id backend '{backend}'; disabled")
            return None
        logger.info(f"[voice] speaker ID active, backend={backend} ({len(recognizer.speaker_names)} enrolled)")
        return recognizer
    except Exception as e:
        logger.warning(f"[voice] speaker ID unavailable ({e}); disabled")
        return None


def _build_speaker_enrollment_service(cfg: AppConfig, audio_capture):
    """Enrollment-side counterpart to _build_speaker_id. None if speaker ID
    is disabled, misconfigured, or the mic adapter failed to build."""
    scfg = cfg.audio.speaker_id
    if not scfg.enabled or audio_capture is None:
        return None
    backend = (scfg.backend or "resemblyzer").lower()
    try:
        from core.constants import PROJECT_ROOT
        from service.speakers.speaker_enrollment_service import SpeakerEnrollmentService
        profiles_dir = PROJECT_ROOT / scfg.profiles_dir
        if backend == "resemblyzer":
            from tpa.speaker.resemblyzer_speaker_enroller import ResemblyzerSpeakerEnroller
            enroller = ResemblyzerSpeakerEnroller(
                profiles_dir=profiles_dir, min_enroll_seconds=scfg.min_enroll_seconds,
            )
        elif backend == "eagle":
            access_key = _picovoice_access_key()
            if not access_key:
                return None
            from tpa.speaker.eagle_speaker_enroller import EagleSpeakerEnroller
            enroller = EagleSpeakerEnroller(access_key=access_key, profiles_dir=profiles_dir)
        else:
            return None
        return SpeakerEnrollmentService(enroller=enroller, audio=audio_capture)
    except Exception as e:
        logger.warning(f"[voice] speaker enrollment unavailable ({e}); disabled")
        return None


def _build_voice_session(cfg: AppConfig, *, audio, capture_gate, supervisor, event_bus, speaker_id=None):
    """Assemble the always-on voice loop. None if any required piece is missing."""
    if not cfg.audio.voice_enabled:
        logger.info("[voice] disabled by config")
        return None

    vad, stt, tts, speaker = _build_vad(cfg), _build_stt(cfg), _build_tts(cfg), _build_speaker(cfg)
    missing = [n for n, v in (("mic", audio), ("vad", vad), ("stt", stt), ("tts", tts), ("speaker", speaker)) if v is None]
    if missing:
        logger.warning(f"[voice] not started - missing: {', '.join(missing)}")
        return None

    wake = _build_wake_word(cfg)
    if wake is None and cfg.audio.wake_word_enabled and (cfg.audio.wake_engine or "none").lower() != "none":
        # Fail closed. A wake word was configured, so "always listening" must never be the fallback.
        logger.error(
            f"[voice] wake engine '{cfg.audio.wake_engine}' is configured but could not be started - "
            "voice listening is DISABLED rather than falling back to listening continuously "
            "(set audio.wake_engine: none to opt in to always-on listening)"
        )
        return None

    from service.voice.utterance_collector import UtteranceCollector
    from service.voice.voice_session import VoiceSession

    v = cfg.audio.vad
    collector = UtteranceCollector(
        vad=vad,
        start_frames=v.start_frames,
        silence_ms=v.silence_ms,
        max_duration_sec=v.max_utterance_sec,
        pre_roll_frames=v.pre_roll_frames,
    )

    def _publish(event: dict) -> None:
        """Surface voice activity on the ambient bus so the UI/CLI can follow it."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        ambient = AmbientEvent(
            kind=EventKind.OBSERVATION,
            description=event.get("text", event["kind"]),
            urgency=Urgency.LOW,
            source="voice",
            dedupe_key=f"voice:{event['kind']}",
            payload=event,
        )
        asyncio.ensure_future(event_bus.publish(ambient), loop=loop)

    return VoiceSession(
        audio=audio, collector=collector, stt=stt, tts=tts, speaker=speaker,
        supervisor=supervisor, capture_gate=capture_gate,
        min_chars=cfg.audio.min_transcript_chars,
        speak_replies=cfg.audio.speak_replies,
        speak_timeout_sec=cfg.audio.speak_timeout_sec,
        post_speak_settle_sec=cfg.audio.post_speak_settle_sec,
        echo_guard=cfg.audio.echo_guard,
        wake_word=wake,
        wake_engine=cfg.audio.wake_engine,
        wake_window_sec=cfg.audio.wake_window_sec,
        speaker_id=speaker_id,
        require_known_speaker=cfg.audio.speaker_id.require_known_speaker,
        speaker_match_threshold=cfg.audio.speaker_id.match_threshold,
        on_event=_publish,
    )


def _build_audit_sink(cfg: AppConfig):
    gcfg = cfg.governance
    if not gcfg.enabled or gcfg.audit.backend != "sqlite":
        return NullAuditSink()
    from core.constants import PROJECT_ROOT
    return SqliteAuditSink(PROJECT_ROOT / gcfg.audit.db_path)


def _build_inference(cfg: AppConfig, governance):
    """Returns (wrapped_client, raw_backend).

    The raw backend is returned alongside because boot-time helpers
    (`verify_ready`, `warmup`) are adapter-specific and deliberately not
    part of InferencePort — the wrappers shouldn't have to know about them.
    """
    icfg = cfg.inference
    # Resolve relative to PROJECT_ROOT, not the process cwd — same pattern
    # _build_stt already uses for its model path. Unexercised until now:
    # the ollama backend never reads model_path at all, so this relative-
    # path bug had no way to surface before llama_cpp was first used.
    model_path = icfg.model_path
    if model_path and not Path(model_path).is_absolute():
        from core.constants import PROJECT_ROOT
        model_path = str(PROJECT_ROOT / model_path)
    primary = build_inference_client(
        backend=icfg.backend,
        ollama_host=icfg.ollama_host,
        model_alias=icfg.model_alias,
        llama_cpp_model_path=model_path,
        n_ctx=icfg.n_ctx,
        n_threads=icfg.n_threads,
        num_batch=icfg.num_batch,
        num_predict=icfg.num_predict,
        temperature=icfg.temperature,
        keep_alive=icfg.keep_alive,
        think=icfg.think,
        timeout=icfg.timeout,
        prompt_cache_mb=icfg.prompt_cache_mb,
    )
    on_success = (lambda name: governance.record_success(name)) if governance else None
    on_failure = (lambda name: governance.record_failure(name)) if governance else None
    # SingleFlight is innermost so the serialisation guarantee holds even
    # when GracefulDegradation retries or switches backends.
    wrapped = GracefulDegradation(
        primary=SingleFlight(primary), on_success=on_success, on_failure=on_failure
    )
    return wrapped, primary


def _build_embedding(cfg: AppConfig):
    ecfg = cfg.embedding
    if not ecfg.enabled:
        return None
    try:
        from core.constants import PROJECT_ROOT
        from tpa.inference.embedding_adapter import FastEmbedProvider

        model_path = None
        if ecfg.model_path:
            path = Path(ecfg.model_path)
            model_path = path if path.is_absolute() else PROJECT_ROOT / path
        cache_dir = None
        if ecfg.cache_dir:
            path = Path(ecfg.cache_dir)
            cache_dir = path if path.is_absolute() else PROJECT_ROOT / path
        provider = FastEmbedProvider(model_id=ecfg.model_id, model_path=model_path, cache_dir=cache_dir)
        logger.info(f"[embedding] model {provider.model_id!r} ready")
        return provider
    except Exception as e:
        logger.warning(f"[embedding] unavailable: {e}")
        return None


# Tool -> provider category, for per-category cache TTLs taken from the tool manifests.
_TOOL_CATEGORY = {"get_weather": "weather", "convert_currency": "fx", "web_search": "search"}
_GEOCODE_TTL_SEC = 24 * 3600


def _build_lookup(cfg: AppConfig, tool_manifests: dict) -> tuple[LookupService, TavilyProvider]:
    allow_list = frozenset(cfg.privacy.online.allowlist)
    http_client = AllowListedHttpClient(allow_list=allow_list)
    registry = FactProviderRegistry(allow_list=allow_list)
    key_env = cfg.privacy.online.search_api_key_env
    search = TavilyProvider(http_client, api_key_provider=lambda: os.environ.get(key_env))
    for provider in (WeatherProvider(http_client), GeocodingProvider(http_client), FxProvider(http_client), search):
        try:
            registry.register(provider)
        except ValueError as e:
            logger.warning(f"[lookup] provider '{provider.category}' not registered: {e}")
    ttl = {_TOOL_CATEGORY[n]: m.cache_ttl_sec for n, m in tool_manifests.items() if n in _TOOL_CATEGORY}
    ttl["geocode"] = _GEOCODE_TTL_SEC
    service = LookupService(registry=registry, allow_list=allow_list, cache=TtlCache(), ttl_by_category=ttl)
    return service, search


def _build_lookup_health(lookup: LookupService, search: TavilyProvider, key_env: str) -> LookupHealthService:
    """Probes for `veda doctor`. One entry per online dependency."""
    from domain.entities.fact_query import FactQuery

    def _probe(category: str, params: dict, describe):
        async def _check() -> str:
            return describe(await lookup.fetch(FactQuery(category=category, params=params)))
        return _check

    return LookupHealthService([
        HealthProbe("geocoding", _probe("geocode", {"name": "Hyderabad"}, lambda a: a.text.split(";")[0])),
        HealthProbe("weather", _probe("weather", {"lat": 17.385, "lon": 78.487}, lambda a: a.text)),
        HealthProbe("currency", _probe("fx", {"from": "USD", "to": "INR"}, lambda a: a.text)),
        HealthProbe(
            "web_search", _probe("search", {"query": "today's date", "topic": "general"}, lambda a: a.text.splitlines()[0][:100]),
            configured=search.is_configured, missing_hint=f"set {key_env} in .env to enable web search",
        ),
    ])


def _build_guardrails(cfg: AppConfig) -> tuple[HookRegistry, PermissionManager]:
    gcfg = cfg.guardrails
    permissions = PermissionManager(
        default_level=gcfg.permissions.default_level,
        approval_timeout=gcfg.permissions.approval_timeout,
    )
    rate_limiter = RateLimiter(
        enabled=gcfg.rate_limiting.enabled,
        global_rpm=gcfg.rate_limiting.global_rpm,
        per_skill_rpm=gcfg.rate_limiting.per_skill_rpm,
    )
    input_validator = InputValidator(max_input_length=gcfg.validators.max_input_length)
    output_validator = OutputValidator(
        block_pii=gcfg.validators.block_pii_in_output,
        block_credentials=gcfg.validators.block_credentials_in_output,
        max_output_length=gcfg.validators.max_output_length,
    )
    from core.constants import PROJECT_ROOT
    audit_logger = AuditLogger(
        log_dir=PROJECT_ROOT / gcfg.audit.log_dir,
        enabled=gcfg.audit.enabled,
        log_inputs=gcfg.audit.log_inputs,
        log_outputs=gcfg.audit.log_outputs,
    )

    hooks = HookRegistry()
    hooks.register(HookEvent.PRE_SKILL, create_permission_check_hook(permissions))
    hooks.register(HookEvent.PRE_SKILL, create_rate_limit_hook(rate_limiter))
    hooks.register(HookEvent.PRE_SKILL, create_input_validator_hook(input_validator))
    hooks.register(HookEvent.POST_SKILL, create_output_validator_hook(output_validator))
    hooks.register(HookEvent.POST_SKILL, create_audit_hook(audit_logger))
    return hooks, permissions


def _build_skills(cfg: AppConfig, hooks: HookRegistry) -> tuple[SkillRegistry, SkillRunner]:
    scfg = cfg.skills
    registry = SkillRegistry()
    registry.register(TerminalSkill(permission_level=scfg.terminal.permission_level, enabled=scfg.terminal.enabled, **scfg.terminal.extra))
    registry.register(FileOpsSkill(permission_level=scfg.file_ops.permission_level, enabled=scfg.file_ops.enabled, **scfg.file_ops.extra))
    return registry, SkillRunner(skill_registry=registry, hook_registry=hooks)


def bootstrap(app: FastAPI) -> None:
    """Build every subsystem and attach it to `app.state`."""
    cfg = load_full_config()
    app.state.full_config = cfg

    event_bus = EventBus()
    app.state.event_bus = event_bus

    approval_broker = ApprovalBroker(bus=event_bus)
    app.state.approval_broker = approval_broker

    audit_sink = _build_audit_sink(cfg)
    governance = build_governance(cfg.governance, audit_sink)
    app.state.governance = governance

    inference_client, inference_backend = _build_inference(cfg, governance)
    app.state.inference_client = inference_client
    app.state.inference_backend = inference_backend
    app.state.embedding_provider = _build_embedding(cfg)

    from tpa.persistence.vector_store import SqliteVectorStore
    from service.memory.memory_indexer import MemoryIndexer
    from service.memory.semantic_recall import SemanticRecall
    vector_store = SqliteVectorStore()
    memory_indexer = MemoryIndexer(vector_store, app.state.embedding_provider)
    semantic_recall = SemanticRecall(
        vector_store, app.state.embedding_provider,
        top_k=cfg.embedding.top_k, min_score=cfg.embedding.min_score,
        sources=tuple(cfg.embedding.sources),
    )
    app.state.vector_store = vector_store
    app.state.memory_indexer = memory_indexer
    app.state.semantic_recall = semantic_recall

    conversation_repo = ConversationRepository()
    app.state.conversation_repo = conversation_repo
    memory_repo = AgentMemoryRepository(conversation_repo=conversation_repo)
    conversation = ConversationManager(repo=conversation_repo, max_history=cfg.app.max_history)
    # Exposed so controller/routes/health.py can actually probe the DB;
    # without this its check silently reported False forever.
    app.state.db_session_factory = SessionLocal
    async def _index_summary(summary_id: int, text: str) -> None:
        await memory_indexer.index("summary", str(summary_id), text)

    summariser = ConversationSummariser(
        repo=conversation_repo,
        client=inference_client,
        summary_model=cfg.inference.model_alias,
        summary_num_predict=cfg.inference.summary_num_predict,
        on_summary=_index_summary if memory_indexer.enabled else None,
    )
    app.state.conversation = conversation
    app.state.memory = memory_repo
    app.state.conversation_summariser = summariser

    async def _reindex_facts() -> None:
        try:
            vector_store.clear("fact")
            for i, fact in enumerate(knowledge.list_facts()):
                await memory_indexer.index("fact", str(i), fact)
        except Exception as e:
            logger.warning(f"[memory-index] fact reindex failed: {e}")

    def _reindex_facts_soon() -> None:
        try:
            asyncio.get_running_loop().create_task(_reindex_facts())
        except RuntimeError:
            pass  # no running loop (e.g. tests) — indexing is best-effort

    knowledge = KnowledgeBase(
        store=JsonKnowledgeStore(),
        on_change=_reindex_facts_soon if memory_indexer.enabled else None,
    )
    app.state.knowledge = knowledge

    from tpa.persistence.repositories.task_repository import SqliteTaskRepository
    from service.tasks.task_service import TaskService
    task_service = TaskService(SqliteTaskRepository())
    app.state.task_service = task_service

    hooks, permissions = _build_guardrails(cfg)
    skill_registry, skill_runner = _build_skills(cfg, hooks)
    from service.skills.builtin.tasks import TasksSkill
    tool_manifests = {m.name: m for m in YamlToolManifestStore().load_all()}
    online = cfg.privacy.online
    lookup_service = lookup_search = None
    if online.enabled:
        lookup_service, lookup_search = _build_lookup(cfg, tool_manifests)
    else:
        tool_manifests = {n: m for n, m in tool_manifests.items() if not m.requires_online}
        logger.info("[lookup] online tools disabled (privacy.online.enabled=false)")
    app.state.tool_manifests = tool_manifests
    skill_registry.register(TasksSkill(
        service=task_service,
        manifest=tool_manifests["tasks"],
        permission_level=cfg.skills.tasks.permission_level,
        enabled=cfg.skills.tasks.enabled,
    ))
    if lookup_service is not None:
        from service.lookup.currency_lookup import CurrencyLookup
        from service.lookup.place_resolver import PlaceResolver
        from service.lookup.search_lookup import SearchLookup
        from service.lookup.weather_lookup import WeatherLookup
        from service.skills.builtin.currency import ConvertCurrencySkill
        from service.skills.builtin.weather import GetWeatherSkill
        from service.skills.builtin.web_search import WebSearchSkill

        places = PlaceResolver(lookup_service, default_place=online.default_place)
        for skill in (
            GetWeatherSkill(WeatherLookup(lookup_service, places), tool_manifests["get_weather"]),
            ConvertCurrencySkill(CurrencyLookup(lookup_service), tool_manifests["convert_currency"]),
            WebSearchSkill(SearchLookup(lookup_service), tool_manifests["web_search"]),
        ):
            skill_registry.register(skill)
    app.state.lookup_service = lookup_service
    app.state.lookup_health = (
        _build_lookup_health(lookup_service, lookup_search, online.search_api_key_env) if lookup_service else None
    )
    app.state.hook_registry = hooks
    app.state.permission_manager = permissions
    app.state.skill_registry = skill_registry
    app.state.skill_runner = skill_runner

    system_control = _build_system_control()
    agent_registry = AgentRegistry()
    trace_repo = SqliteTraceRepository()
    app.state.trace_repo = trace_repo
    guard = ToolUseGuard(tool_manifests.values())
    count_tokens = getattr(inference_backend, "count_tokens", None) or estimate_tokens
    composer = PromptComposer(
        FilePromptStore(), list(tool_manifests.values()), budgets=PromptBudgets(), count_tokens=count_tokens,
    )
    tool_runner = ToolTurnRunner(
        client=inference_client, composer=composer, skill_runner=skill_runner,
        manifests=tool_manifests, guard=guard, conversation=conversation,
        model=cfg.agents.responder.model,
    )
    responder = ResponderAgent(
        client=inference_client, conversation=conversation, knowledge=knowledge,
        model=cfg.agents.responder.model, memory=memory_repo,
        recall=semantic_recall, claim_filter=guard.claims_action,
    )
    system_agent = SystemAgent(
        client=inference_client, system_control=system_control,
        model=cfg.agents.system.model, governance=governance, memory=memory_repo,
    )
    agent_registry.register(responder)
    agent_registry.register(system_agent)
    if cfg.agents.tools_enabled:
        # One specialist per owning agent named in the tool manifests. Routing
        # triggers, tools and guard rules all come from the manifests.
        for owner in sorted({m.agent for m in tool_manifests.values()}):
            owned = composer.tools_of_agent(owner)
            agent_registry.register(ToolAgent(
                name=owner,
                description=(
                    getattr(cfg.agents, owner, None).description
                    if getattr(cfg.agents, owner, None) is not None and getattr(cfg.agents, owner).description
                    else "; ".join(tool_manifests[n].description for n in owned)
                ),
                tool_names=owned,
                triggers=[t for n in owned for t in tool_manifests[n].triggers],
                runner=tool_runner, fallback=responder, guard=guard,
                conversation=conversation, memory=memory_repo,
                model=cfg.agents.responder.model, traces=trace_repo,
            ))

    llm_router = None
    if cfg.agents.llm_routing:
        llm_router = LlmRouter(
            client=inference_client, composer=composer, model=cfg.agents.supervisor.model,
            num_predict=min(cfg.inference.routing_num_predict, 24), history=lambda: conversation.turns,
        )
    supervisor = SupervisorAgent(
        agent_registry=agent_registry, client=inference_client, default_agent="responder",
        model=cfg.agents.supervisor.model, debounce_window_sec=cfg.sensing.debounce_window_sec,
        rate_limit_max=cfg.sensing.rate_limit_max, rate_limit_window_sec=cfg.sensing.rate_limit_window_sec,
        proactivity=cfg.sensing.proactivity, memory=memory_repo, governance=governance,
        conversation=conversation,
        routing_num_predict=cfg.inference.routing_num_predict, llm_router=llm_router,
    )
    agent_registry.register(supervisor)
    app.state.agent_registry = agent_registry
    app.state.supervisor = supervisor

    app.state.notifier = _build_notifier()
    app.state.egress_allow_list = frozenset(cfg.privacy.online.allowlist)

    # Privacy chain (REQ-M-04/M-05). CaptureGate owns mute state and is the
    # only thing allowed to start/stop the mic or drive the indicator.
    # The same capture adapter instance is shared with the voice loop —
    # two independent mic streams would fight over the device.
    mute_switch = _build_mute_switch(cfg)
    audio_capture = _build_audio_capture(cfg)
    capture_gate = CaptureGate(
        mute_switch=mute_switch,
        indicator=_build_indicator(),
        audio=audio_capture,
        bus=event_bus,
    )
    app.state.mute_switch = mute_switch
    app.state.capture_gate = capture_gate

    # Multi-speaker recognition (REQ-M-04 extension: "don't react to stray
    # talk" sharpened to "don't react to an unenrolled voice"). Built once
    # here so the live recognizer instance is reachable both from the voice
    # loop and from the enrollment route's reload_profiles() call.
    speaker_recognizer = _build_speaker_id(cfg)
    app.state.speaker_recognizer = speaker_recognizer
    app.state.speaker_enrollment_service = _build_speaker_enrollment_service(cfg, audio_capture)

    # Always-on voice loop. Gated by CaptureGate, so building it does not
    # mean the mic is open — it opens only when the gate says unmuted.
    app.state.voice_session = _build_voice_session(
        cfg, audio=audio_capture, capture_gate=capture_gate,
        supervisor=supervisor, event_bus=event_bus, speaker_id=speaker_recognizer,
    )

    logger.info(f"Bootstrap: {agent_registry.count} agents, {len(skill_registry.list_all())} skills registered")


async def _backfill_memory_index(app: FastAPI) -> None:
    """Index existing facts and summaries once at startup (best-effort, background)."""
    indexer = getattr(app.state, "memory_indexer", None)
    store = getattr(app.state, "vector_store", None)
    if indexer is None or store is None or not indexer.enabled:
        return
    try:
        store.clear("fact")
        for i, fact in enumerate(app.state.knowledge.list_facts()):
            await indexer.index("fact", str(i), fact)
        for summ in app.state.conversation_repo.recent_summaries(limit=1000):
            await indexer.index("summary", str(summ.id), summ.content, skip_existing=True)
        logger.info("[memory-index] backfill complete")
    except Exception as e:
        logger.warning(f"[memory-index] backfill failed: {e}")


async def _housekeeping_loop(task_service, trace_repo, interval_sec: float = 3600.0) -> None:
    """Hourly: drop completed tasks and old tool-turn traces past their retention windows."""
    from datetime import datetime, timedelta

    from domain.policies.retention_policy import TURN_TRACE_RETENTION_DAYS

    while True:
        try:
            removed = task_service.purge_completed()
            if removed:
                logger.info(f"[tasks] purged {removed} completed task(s)")
            old = trace_repo.purge_before(datetime.now() - timedelta(days=TURN_TRACE_RETENTION_DAYS))
            if old:
                logger.info(f"[trace] purged {old} old tool-turn trace(s)")
        except Exception as e:
            logger.warning(f"[housekeeping] failed: {e}")
        await asyncio.sleep(interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
    cfg = load_full_config()
    configure_logging(cfg.app.log_level)
    ensure_dirs()
    init_tables()

    bootstrap(app)

    # Fail loudly here if the configured model isn't importable, rather than
    # 404-ing on the user's first turn with an opaque error.
    client = app.state.inference_backend
    if cfg.inference.verify_on_boot and hasattr(client, "verify_ready"):
        try:
            await client.verify_ready()
            logger.info(f"[inference] model {cfg.inference.model_alias!r} ready on {cfg.inference.backend}")
        except Exception as e:
            logger.error(f"[inference] NOT READY: {e}")

    if cfg.inference.warmup_on_boot and hasattr(client, "warmup"):
        try:
            await client.warmup()
            logger.info("[inference] warmed up")
        except Exception as e:
            logger.warning(f"[inference] warmup skipped: {e}")

    # Must start inside the running loop — the gate captures it so a
    # mute transition raised on a hardware thread can still reach the bus.
    try:
        app.state.capture_gate.start()
    except Exception as e:
        logger.error(f"CaptureGate failed to start: {e}")

    try:
        app.state.conversation_summariser.start()
    except Exception as e:
        logger.warning(f"ConversationSummariser failed to start: {e}")

    app.state.task_purge_job = asyncio.create_task(_housekeeping_loop(app.state.task_service, app.state.trace_repo))

    if getattr(app.state, "memory_indexer", None) is not None and app.state.memory_indexer.enabled:
        asyncio.create_task(_backfill_memory_index(app))

    if app.state.voice_session is not None:
        try:
            app.state.voice_session.start()
        except Exception as e:
            logger.error(f"VoiceSession failed to start: {e}")

    yield

    if getattr(app.state, "voice_session", None) is not None:
        try:
            await app.state.voice_session.stop()
        except Exception as e:
            logger.warning(f"VoiceSession failed to stop cleanly: {e}")

    app.state.task_purge_job.cancel()
    try:
        await app.state.conversation_summariser.stop()
    except Exception:
        pass
    try:
        app.state.capture_gate.stop()
    except Exception as e:
        logger.warning(f"CaptureGate failed to stop cleanly: {e}")

    recognizer = getattr(app.state, "speaker_recognizer", None)
    if recognizer is not None:
        try:
            recognizer.stop()
        except Exception:
            pass

    from core.constants import TEMP_DIR
    if TEMP_DIR.exists():
        for f in TEMP_DIR.iterdir():
            f.unlink(missing_ok=True)


app = FastAPI(title=PROJECT_NAME, version=VERSION, lifespan=lifespan)
register_exception_handlers(app)
app.middleware("http")(request_context)
logger.info(f"{PROJECT_NAME} v{VERSION} application initialized")

for _mod in (
    admin, ambient, approval, chat, config_route, governance_route,
    health, knowledge, lookup, memory_route, persona, privacy, speakers, stream, system, tasks_route, trace_route, voice,
):
    app.include_router(_mod.router, prefix="/api")


@app.get("/", include_in_schema=False)
async def _root() -> dict:
    """Headless build — the Pi target has no browser (ADR-008). The API and
    the `veda` CLI are the interfaces; the Angular SPA was removed."""
    return {
        "name": PROJECT_NAME,
        "version": VERSION,
        "interfaces": ["REST /api/*", "CLI: veda"],
        "docs": "/docs",
    }