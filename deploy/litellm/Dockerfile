# CottonMouth LiteLLM gateway = the official LiteLLM proxy + the CottonMouth SDK,
# so config.yaml can load the observability callback:
#   litellm_settings.callbacks: cottonmouth.integrations.litellm.cottonmouth_callback
#
# Build from the repo root so the build context can see sdk/:
#   docker build -f deploy/litellm/Dockerfile -t <repo>/cottonmouth-litellm:vN .
FROM ghcr.io/berriai/litellm:main-stable

# The SDK core is dependency-free; litellm is already present in this image, so we
# install without the [litellm] extra to avoid disturbing the pinned version.
COPY sdk /sdk
# The official image ships a uv-built venv with neither pip nor uv on PATH, so
# bootstrap pip via ensurepip, then install the (dependency-free) SDK into it.
RUN python -m ensurepip --upgrade && python -m pip install --no-cache-dir /sdk

# Entry/args (config + port) are provided by the Deployment.
