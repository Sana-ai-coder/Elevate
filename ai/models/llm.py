"""Language model wrapper for LOCAL text generation on CPU using Persistent Storage."""
from __future__ import annotations
import os
import threading
from typing import Optional
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from config import MODELS_DIR

LLM_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"

class LanguageModel:
    def __init__(self) -> None:
        self.device = "cpu"
        print(f"Loading LOCAL model: {LLM_MODEL} into memory...")
        print(f"Using persistent storage cache: {MODELS_DIR}")
        
        # This downloads the model to /data ONLY on the very first run.
        # On all future runs, it boots instantly from your persistent storage.
        self.tokenizer = AutoTokenizer.from_pretrained(
            LLM_MODEL, 
            cache_dir=str(MODELS_DIR)
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            LLM_MODEL,
            cache_dir=str(MODELS_DIR),
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True
        )
        self.model.eval()
        print("Local language model loaded successfully.")

    def ensure_model_loaded(self) -> None:
        pass

    def is_model_loaded(self) -> bool:
        return True

    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 800,
        temperature: float = 0.1,
        top_p: float = 0.95,
        max_time: Optional[float] = None,
    ) -> str:
        messages = [
            {"role": "system", "content": "You are an expert educational AI. Generate exactly what is requested, following the text format perfectly."},
            {"role": "user", "content": prompt}
        ]
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer([text], return_tensors="pt").to(self.device)
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                do_sample=True,
                top_p=top_p,
                pad_token_id=self.tokenizer.eos_token_id
            )
            
        generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in zip(inputs.input_ids, outputs)]
        response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return response.strip()

_llm_model: LanguageModel | None = None
_llm_model_lock = threading.Lock()

def get_llm_model() -> LanguageModel:
    global _llm_model
    if _llm_model is None:
        with _llm_model_lock:
            if _llm_model is None:
                _llm_model = LanguageModel()
    return _llm_model