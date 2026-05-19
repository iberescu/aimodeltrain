FROM vllm/vllm-openai:latest

# Pre-download the private LoRA adapter at container start (not build time),
# so the HF_TOKEN Space secret is available. Then exec vLLM with the right
# flags for our setup.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# HF Spaces convention: app must listen on the port declared in README's
# `app_port:` (7860 by default). We honor that via the vLLM --port flag.
EXPOSE 7860

ENTRYPOINT ["/entrypoint.sh"]
