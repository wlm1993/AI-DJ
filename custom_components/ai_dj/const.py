"""Constants for the AI DJ integration."""

DOMAIN = "ai_dj"

CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_LOOKAHEAD = "lookahead"

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDERS = [PROVIDER_ANTHROPIC, PROVIDER_OPENAI]

DEFAULT_MODELS = {
    PROVIDER_ANTHROPIC: "claude-haiku-4-5",
    PROVIDER_OPENAI: "gpt-4o-mini",
}
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

CARD_URL_PATH = f"/{DOMAIN}-files/ai-dj-card.js"
