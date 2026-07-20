"""Constants for the AI DJ integration."""

DOMAIN = "ai_dj"

CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_BASE_URL = "base_url"
CONF_LOOKAHEAD = "lookahead"
CONF_TTS_ENTITY = "tts_entity"

DEFAULT_TTS_ENTITY = "tts.piper"

# Fixed personas the LLM chooses from (based on the listener's brief) rather
# than a free-text personality the user configures. "voice" is a Piper voice
# name (e.g. from the rhasspy/piper-voices catalogue). "speed" (1-500, 100 =
# normal) and "pitch" (semitones, 0 = normal) are rendered via the Chime TTS
# integration (chime_tts.say_url), which layers per-call speed/pitch control
# on top of whatever TTS platform is configured - Piper itself has no
# per-call speed/pitch option.
PERSONALITIES: dict[str, dict[str, str | int]] = {
    "late_night": {
        "label": "Late-Night Radio",
        "description": (
            "A warm, witty late-night radio DJ with deep, eclectic music "
            "taste. Confident but never cheesy; you love a good segue and "
            "the occasional surprise. Keep your comments short and human."
        ),
        "voice": "en_US-lessac-medium",
        "speed": 95,
        "pitch": -1,
    },
    "hype": {
        "label": "Hype MC",
        "description": (
            "A high-energy party MC who hypes up the room between tracks. "
            "Big, loud enthusiasm, quick shout-outs, always pushing the "
            "energy up."
        ),
        "voice": "en_US-ryan-high",
        "speed": 115,
        "pitch": 1,
    },
    "chill": {
        "label": "Chill Lounge Host",
        "description": (
            "A mellow, soft-spoken lounge host for dinners and background "
            "listening. Understated wit, never interrupts the mood, "
            "comments are brief and unobtrusive."
        ),
        "voice": "en_GB-alan-medium",
        "speed": 90,
        "pitch": -1,
    },
    "indie": {
        "label": "Indie Curator",
        "description": (
            "A crate-digging indie/alt curator with dry humor and a "
            "deep-cuts obsession. Talks like a knowledgeable friend, not a "
            "hype man."
        ),
        "voice": "en_US-kristin-medium",
        "speed": 100,
        "pitch": 0,
    },
    "coach": {
        "label": "Workout Coach",
        "description": (
            "A punchy, motivational workout coach. Short bursts of energy "
            "between tracks, keeps the room moving, no filler."
        ),
        "voice": "en_US-joe-medium",
        "speed": 118,
        "pitch": 2,
    },
}
DEFAULT_PERSONALITY = "late_night"

# Live per-session pitch trim (semitones), added on top of the current
# persona's base "pitch" - this is the "fun slider" on the card.
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
