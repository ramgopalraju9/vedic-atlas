"""InferenceFactory — picks the local inference backend from config.

★ New. Rewritten from veda/brain/factory.py's build_llm_client shape
(PATH-probing between claude/copilot CLIs), per ADR-001: no CLI
probing — the backend is an explicit config choice between "ollama" and
"llama_cpp", never a cloud backend. Fails loudly (ConfigError-equivalent)
if a cloud backend is requested at all, rather than silently ignoring
the setting — this is the enforcement point for REQ-M-03 at the
composition boundary.
"""

from __future__ import annotations

from domain.ports.inference_port import InferencePort


class UnsupportedBackendError(ValueError):
    pass


def build_inference_client(
    backend: str,
    *,
    ollama_host: str = "http://127.0.0.1:11434",
    model_alias: str = "qwen-wire",
    llama_cpp_model_path: str | None = None,
    n_ctx: int = 4096,
    n_threads: int | None = None,
    num_batch: int | None = None,
    num_predict: int = 512,
    temperature: float = 0.7,
    keep_alive: str = "30m",
    think: bool = False,
    timeout: float = 120.0,
    prompt_cache_mb: int = 0,
    model_lock=None,
) -> InferencePort:
    backend = backend.lower().strip()

    if backend in ("claude", "copilot", "hybrid_cloud"):
        raise UnsupportedBackendError(
            f"backend '{backend}' is a cloud backend and is not permitted in this build "
            "(REQ-M-02/M-03: all reasoning must be on-device)"
        )

    if backend == "ollama":
        from tpa.inference.ollama_client import OllamaClient

        return OllamaClient(
            host=ollama_host,
            model=model_alias,
            timeout=timeout,
            keep_alive=keep_alive,
            num_ctx=n_ctx,
            num_predict=num_predict,
            num_thread=n_threads,
            num_batch=num_batch,
            temperature=temperature,
            think=think,
        )

    if backend == "llama_cpp":
        if not llama_cpp_model_path:
            raise UnsupportedBackendError("backend 'llama_cpp' requires llama_cpp_model_path to be set")
        from tpa.inference.llama_cpp_client import LlamaCppClient

        return LlamaCppClient(
            model_path=llama_cpp_model_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            timeout=timeout,
            num_predict=num_predict,
            think=think,
            prompt_cache_mb=prompt_cache_mb,
            model_lock=model_lock,
        )

    raise UnsupportedBackendError(f"unknown backend '{backend}' — expected 'ollama' or 'llama_cpp'")