"""Language model wrapper for local text generation (CPU/GPU)."""

from __future__ import annotations

import os
import threading
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from config import (
    HF_TOKEN,
    LLM_MODEL,
    LORA_ADAPTER_PATH,
    MAX_NEW_TOKENS,
    MAX_PROMPT_TOKENS,
    MODELS_DIR,
    TEMPERATURE,
    TOP_P,
)

try:
    from peft import PeftModel
except Exception:
    PeftModel = None


def _resolve_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


class LanguageModel:
    def __init__(self) -> None:
        self.device = _resolve_device()
        print(f"Loading local language model: {LLM_MODEL} on {self.device}...")
        print(f"Model cache directory: {MODELS_DIR}")

        if self.device == "cuda":
            try:
                gpu_name = torch.cuda.get_device_name(0)
                print(f"Detected GPU: {gpu_name}")
            except Exception:
                pass

        try:
            cpu_count = max(1, os.cpu_count() or 1)
            torch.set_num_threads(min(cpu_count, 8))
        except Exception:
            pass

        quantization_config = None
        if self.device == "cuda":
            try:
                quantization_config = BitsAndBytesConfig(
                    load_in_8bit=True,
                    llm_int8_threshold=6.0,
                )
            except Exception:
                quantization_config = None

        self.tokenizer = AutoTokenizer.from_pretrained(
            LLM_MODEL,
            cache_dir=str(MODELS_DIR),
            token=HF_TOKEN or None,
            use_fast=True,
            trust_remote_code=True,
        )

        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.model = AutoModelForCausalLM.from_pretrained(
            LLM_MODEL,
            cache_dir=str(MODELS_DIR),
            token=HF_TOKEN or None,
            quantization_config=quantization_config,
            device_map="auto" if self.device == "cuda" else None,
            dtype=torch.float16 if self.device == "cuda" else torch.float32,
            low_cpu_mem_usage=True,
            trust_remote_code=True,
        )

        if LORA_ADAPTER_PATH and PeftModel is not None:
            try:
                print(f"Loading LoRA adapter: {LORA_ADAPTER_PATH}")
                self.model = PeftModel.from_pretrained(self.model, LORA_ADAPTER_PATH)
                print("LoRA adapter loaded successfully.")
            except Exception as exc:
                print(f"Warning: failed to load LoRA adapter '{LORA_ADAPTER_PATH}': {exc}")

        if self.device == "cpu":
            self.model = self.model.to(self.device)

        try:
            if getattr(self.model, "generation_config", None) is not None:
                self.model.generation_config.max_length = None
        except Exception:
            pass

        self.model.eval()
        print("Local language model loaded successfully.")

    def _format_chat_prompt(self, prompt: str) -> str:
        model_name = (LLM_MODEL or "").lower()
        is_chat = ("chat" in model_name) or ("instruct" in model_name)
        if is_chat:
            return (
                "<|system|>\n"
                "You are an expert educational AI that strictly follows instructions."
                "</s>\n<|user|>\n"
                f"{prompt}</s>\n<|assistant|>\n"
            )
        return prompt

    def generate(
        self,
        prompt: str,
        max_new_tokens: int = MAX_NEW_TOKENS,
        temperature: float = TEMPERATURE,
        top_p: float = TOP_P,
        max_time: Optional[float] = None,
    ) -> str:
        formatted_prompt = self._format_chat_prompt(prompt)
        inputs = self.tokenizer(
            formatted_prompt,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_PROMPT_TOKENS,
        ).to(self.model.device)

        do_sample = bool(temperature and temperature > 0.0)
        generate_kwargs = {
            "max_new_tokens": max_new_tokens,
            "do_sample": do_sample,
            "pad_token_id": self.tokenizer.pad_token_id,
            "eos_token_id": self.tokenizer.eos_token_id,
        }
        if do_sample:
            generate_kwargs["temperature"] = temperature
            generate_kwargs["top_p"] = top_p
        if max_time is not None and max_time > 0:
            generate_kwargs["max_time"] = float(max_time)

        with torch.inference_mode():
            outputs = self.model.generate(
                **inputs,
                **generate_kwargs,
            )

        generated_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        prompt_text = self.tokenizer.decode(inputs["input_ids"][0], skip_special_tokens=True)

        if generated_text.startswith(prompt_text):
            generated_text = generated_text[len(prompt_text):].strip()

        if "<|assistant|>" in generated_text:
            generated_text = generated_text.split("<|assistant|>")[-1].strip()

        return generated_text.strip()


_llm_model: LanguageModel | None = None
_llm_model_lock = threading.Lock()


def get_llm_model() -> LanguageModel:
    global _llm_model
    if _llm_model is None:
        with _llm_model_lock:
            if _llm_model is None:
                _llm_model = LanguageModel()
    return _llm_model
