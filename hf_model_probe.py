"""One-off diagnostic: which small instruct/chat models are actually
servable right now via HF Inference Providers on this account, instead of
guessing model IDs one at a time and hitting model_not_supported errors
(happened twice already this session: TinyLlama, then Qwen2.5-7B-Instruct).
"""
from huggingface_hub import list_models

models = list_models(
    pipeline_tag="text-generation",
    inference_provider="hf-inference",
    sort="downloads",
    limit=20,
)

for m in models:
    print(m.id)
