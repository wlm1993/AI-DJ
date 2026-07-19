# AI DJ

An LLM-powered DJ for Home Assistant + [Music Assistant](https://music-assistant.io/).

Tell it what you're in the mood for and it runs a live session on any Music
Assistant speaker: it picks real tracks from your library, keeps a few queued
ahead, and re-plans on the fly as you **like** tracks, **wish** for songs, or
ask for a **change of mood** — like a DJ taking requests.

## How it works

- A rolling queue: the DJ keeps N tracks (default 3) queued ahead. Every time
  a track starts playing, it asks the LLM for the next pick given the full
  session context (your brief, likes, wishes, and everything already played).
- Wishes jump the queue: a wish is resolved immediately and inserted next.
- Likes steer the session: liked tracks bias all future picks.
- Track suggestions are resolved against your Music Assistant library via
  `music_assistant.search`, preferring plain studio versions over
  live/remix/karaoke cuts. Suggestions that aren't in your library are
  silently skipped (the DJ always asks for spare candidates).

Works with **Anthropic (Claude)** or **OpenAI** — you pick the provider and
model during setup.

## Requirements

- Home Assistant 2024.6 or newer
- The [Music Assistant integration](https://www.home-assistant.io/integrations/music_assistant/) set up and working
- An Anthropic or OpenAI API key

## Installation (HACS)

1. Push this repository to GitHub.
2. HACS → three-dot menu → **Custom repositories** → add the repo URL,
   category **Integration**.
3. Install **AI DJ**, restart Home Assistant.
4. Settings → Devices & Services → **Add Integration** → **AI DJ**.
   Pick your provider, paste the API key (validated live), accept or change
   the default model.

## The card

The Lovelace card is bundled and registered automatically — no resource
setup needed. Add it to any dashboard:

```yaml
type: custom:ai-dj-card
```

(It reads `sensor.ai_dj` by default; override with `entity:` if needed.)

- **Idle:** type a prompt, pick a speaker, hit *Start the DJ*.
- **On air:** now playing with album art, ♥ like, ⏭ skip, ⏸ pause,
  ⏹ end session, a wish box, the DJ's one-line commentary, the upcoming
  queue, and your liked tracks.

## Services

Everything the card does is also a service, so automations and voice
assistants can drive the DJ too:

| Service | Fields | What it does |
|---|---|---|
| `ai_dj.start` | `prompt`, `player` | Start a session on a Music Assistant player |
| `ai_dj.stop` | — | End the session (queued tracks keep playing) |
| `ai_dj.like` | — | Like the current track — more of this, please |
| `ai_dj.wish` | `text` | Song wish or mood change; plays next |
| `ai_dj.skip` | — | Skip to the next track |

Example — a "dinner party" script:

```yaml
script:
  dinner_dj:
    sequence:
      - action: ai_dj.start
        data:
          prompt: "Warm dinner-party grooves — soul, bossa, quiet funk. Keep it smooth."
          player: media_player.kokken_2
```

## Options

Settings → Devices & Services → AI DJ → **Configure**:

- **Model** — any model id your provider offers.
- **Tracks queued ahead** (1–6) — bigger is smoother but reacts slower to
  likes and mood changes.

## Notes

- One session runs at a time; starting a new one replaces the old.
- The session state lives in `sensor.ai_dj` (`idle`/`active`, with
  `current_track`, `upcoming`, `liked`, `wishes`, `history`, `dj_comment`
  attributes) — easy to build automations on.
- Only the search/pick round-trips hit the LLM API; a typical evening is a
  few dozen small calls to a cheap model.
