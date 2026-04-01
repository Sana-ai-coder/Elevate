"""Model wrapper for the Hugging Face Serverless Inference API."""
from __future__ import annotations
import json
import os
from typing import Optional
import urllib.request
import urllib.error

# The model is defined here, but the code is flexible enough to allow
# environment variables to override it.
DEFAULT_INFERENCE_API_MODEL = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"

class LanguageModel:
    """A client for the Hugging Face serverless Inference API."""
    def __init__(self, model: Optional[str] = None):
        self.model_id = (
            model
            or os.environ.get("INFERENCE_API_MODEL")
            or DEFAULT_INFERENCE_API_MODEL
        )
        self.api_url = f"https://api-inference.huggingface.co/models/{self.model_id}"
        
        # 1. FIX: Properly handle missing tokens
        self.api_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        
        # 2. FIX: Add Content-Type to headers
        self.headers = {
            "Content-Type": "application/json"
        }
        
        if self.api_token:
            self.headers["Authorization"] = f"Bearer {self.api_token}"
        
        # This is a client, so it's always "loaded" and runs on the "cloud".
        self.device = "cloud"
        print(f"Using Hugging Face Inference API for model: {self.model_id}")

    def ensure_model_loaded(self) -> None:
        """This is a no-op as the model is managed by Hugging Face."""
        pass

    def is_model_loaded(self) -> bool:
        """Always returns True for the API client."""
        return True

    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.95,
        max_time: Optional[float] = None,
    ) -> str:
        """
        Calls the Inference API to generate text.
        """
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": max_new_tokens,
                "temperature": max(0.01, temperature),  # Temp must be > 0
                "top_p": top_p,
                "return_full_text": False,  # We only want the generated part
            },
        }
        
        request_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url, data=request_body, headers=self.headers
        )
        
        timeout = max_time if max_time is not None else 30.0
        
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode())
                
                if isinstance(result, list):
                    return result[0].get("generated_text", "")
                elif isinstance(result, dict) and "error" in result:
                    print(f"[Inference API Error] {result['error']}")
                    return ""  # Return empty on error
                else:
                    return ""
                    
        # 3. FIX: Catch explicit HTTP errors to log them accurately instead of a generic fail
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            print(f"[Inference API HTTP Error] Code: {e.code}, Reason: {e.reason}, Body: {error_body}")
            return ""
        except Exception as e:
            print(f"[Inference API Request Failed] {e}")
            return ""

_llm_model: LanguageModel | None = None

def get_llm_model() -> LanguageModel:
    """Singleton factory for the language model client."""
    global _llm_model
    if _llm_model is None:
        _llm_model = LanguageModel()
    return _llm_model