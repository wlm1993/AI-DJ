"""Constants for the AI DJ integration."""

DOMAIN = "ai_dj"

CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_BASE_URL = "base_url"
CONF_LOOKAHEAD = "lookahead"
CONF_PERSONALITY = "personality"

DEFAULT_PERSONALITY = (
    "A warm, witty late-night radio DJ with deep, eclectic music taste. "
    "Confident but never cheesy; you love a good segue and the occasional "
    "surprise. Keep your comments short and human."
)

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDERS = [
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI_COMPATIBLE,
]

# Providers that need a user-supplied base URL (OpenAI-compatible endpoints).
PROVIDERS_NEED_BASE_URL = [PROVIDER_OPENAI_COMPATIBLE]

DEFAULT_MODELS = {
    PROVIDER_ANTHROPIC: "claude-haiku-4-5",
    PROVIDER_OPENAI: "gpt-4o-mini",
    PROVIDER_GEMINI: "gemini-2.5-flash",
    PROVIDER_OPENAI_COMPATIBLE: "llama-3.3-70b-versatile",
}

# Suggested base URL prefilled for the OpenAI-compatible step (Groq shown as
# a fast, cheap default; swap for OpenRouter, DeepSeek, Ollama, etc.).
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_LOOKAHEAD = 3
MIN_LOOKAHEAD = 1
MAX_LOOKAHEAD = 6

# How many extra candidates to request beyond what we need, so that
# tracks the library search can't resolve don't leave the queue short.
EXTRA_CANDIDATES = 4

SERVICE_START = "start"
SERVICE_STOP = "stop"
SERVICE_LIKE = "like"
SERVICE_WISH = "wish"
SERVICE_SKIP = "skip"

ATTR_PROMPT = "prompt"
ATTR_PLAYER = "player"
ATTR_TEXT = "text"

SIGNAL_SESSION_UPDATE = f"{DOMAIN}_session_update"

CARD_VERSION = "0.2.0"
CARD_URL_PATH = f"/{DOMAIN}-files/ai-dj-card.js"
# Version query busts the browser/Cloudflare cache when the card changes.
CARD_RESOURCE_URL = f"{CARD_URL_PATH}?v={CARD_VERSION}"
