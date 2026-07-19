# LiteLLM gateway (optional)

This API does not yet call LLMs in the main request path. When it does, use:

```env
LITELLM_BASE_URL=http://localhost:4000/v1
LITELLM_VIRTUAL_KEY=sk-...
LITELLM_MODEL=llm-general
```

Prefer the virtual key + gateway over raw `OPENAI_API_KEY` in this service.
Gateway repo: `litellm-langfuse-gateway` (local Docker stack + Langfuse).
