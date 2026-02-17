# LiteLLM proxy with entrypoint that loads keys from files (no print).
FROM docker.litellm.ai/berriai/litellm:main-latest
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
