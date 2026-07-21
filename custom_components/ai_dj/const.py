"""Constants for the AI DJ integration."""

DOMAIN = "ai_dj"

CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_BASE_URL = "base_url"
CONF_LOOKAHEAD = "lookahead"
CONF_TTS_ENTITY = "tts_entity"

DEFAULT_TTS_ENTITY = "tts.piper"

# The DJ's one and only persona - fed to the LLM as "dj_personality".
DJ_PERSONALITY = (
    "A fast-talking motormouth radio DJ with a razor tongue. You hang on "
    "every word the listener gives you - their brief, their wishes, the "
    "tracks they liked - and work those exact words back into your patter, "
    "then use them to take the piss out of them. You roast the listener "
    "hard: their taste, their mood, whatever they just asked for. Think "
    "insult comic behind the decks - sarcastic, cutting, rude, crude and "
    "profane when it lands. Never flatter them. Talk fast and punchy: one "
    "breathless sentence, never a monologue."
)

# How that persona is voiced. DJ_VOICE is a Piper voice name (from the
# rhasspy/piper-voices catalogue); DJ_SPEED (1-500, 100 = normal) and
# DJ_PITCH (semitones, 0 = normal) are applied by the Chime TTS integration
# when the comment is read aloud - Piper has no per-call speed/pitch option.
# The speed runs hot to match the motormouth delivery.
DJ_VOICE = "en_US-ryan-high"
DJ_SPEED = 130
DJ_PITCH = 0

# Live per-session pitch trim (semitones), added on top of DJ_PITCH -
# this is the "fun slider" on the card.
MIN_ANNOUNCE_PITCH = -12
MAX_ANNOUNCE_PITCH = 12

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
SERVICE_SET_ANNOUNCE = "set_announce"
SERVICE_SET_ANNOUNCE_PITCH = "set_announce_pitch"

ATTR_PROMPT = "prompt"
ATTR_PLAYER = "player"
ATTR_TEXT = "text"
ATTR_ENABLED = "enabled"
ATTR_PITCH = "pitch"

SIGNAL_SESSION_UPDATE = f"{DOMAIN}_session_update"

CARD_VERSION = "0.6.0"
CARD_URL_PATH = f"/{DOMAIN}-files/ai-dj-card.js"
# Version query busts the browser/Cloudflare cache when the card changes.
CARD_RESOURCE_URL = f"{CARD_URL_PATH}?v={CARD_VERSION}"
