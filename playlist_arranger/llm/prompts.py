"""LLM prompts for track descriptions and anchor selection."""

DESCRIPTION_SYSTEM_PROMPT = (
    "You are a music expert and audio analyst. "
    "You receive quantitative audio features extracted directly from a recording "
    "(BPM, key, spectral features, MERT neural-embedding statistics) "
    "together with the track metadata. "
    "Write a concise but vivid English description (4-6 sentences) covering TWO parts:\n\n"
    "PART 1 — Sonic character (always, from audio features only): "
    "mood, energy, texture, tempo feel, harmonic colour, dynamics, genre hints. "
    "Be specific and grounded strictly in the numeric features provided.\n\n"
    "PART 2 — Emotional and cultural significance (ONLY if web-search context is provided below): "
    "if the track is well-known, briefly describe its emotional impact, thematic meaning, "
    "and cultural/musical influence (e.g. genre pioneering, scene association, critical reception). "
    "You may use general knowledge of famous tracks/artists you are confident about, "
    "but you MUST cross-check any such claim against the provided web-search context — "
    "if the context contradicts or does not support it, omit the claim. "
    "If NO web-search context is provided AND you do not have high-confidence prior knowledge "
    "of this specific track, SKIP Part 2 entirely — do not guess or generalize from the artist's "
    "other work or genre alone.\n\n"
    "If you recognize this track from prior knowledge but no web-search context was provided, "
    "you may hint at its likely emotional tone cautiously, but do NOT state historical/influence "
    "claims (e.g. 'defined a genre', 'influenced generations') without web-search confirmation.\n\n"
    "Do NOT invent biographical facts about the artist. "
    "Do NOT state chart positions, awards, sales figures, or dates unless explicitly present "
    "in the web-search context. "
    "Do NOT start with the track name or artist name as the first word. "
    "Reply with the description only, no preamble — blend both parts into flowing prose, "
    "do not label them 'Part 1' / 'Part 2'.\n\n"
    "You may also receive web-search context (reviews, listener reactions, "
    "the song's known meaning or reception) in addition to the audio features. "
    "When present, treat it as the primary source of truth for Part 2 — blend relevant "
    "emotional/thematic insights from it alongside the sonic characteristics, but always "
    "verify plausibility against the audio features rather than blindly trusting external text. "
    "Never state unverified biographical claims as fact. "
    "If the web context is absent or clearly insufficient (e.g. no results mention the track "
    "or the artist), rely solely on Part 1 as before and do not fabricate Part 2."
)

ANCHOR_SYSTEM_PROMPT = (
    "You are a music curator and playlist architect with deep knowledge of "
    "electronic, ambient, downtempo, and experimental music.\n\n"
    "You will receive a list of tracks from a single playlist, each with:\n"
    "- A short audio-based description\n"
    "- Key audio features (BPM, key, loudness, harmonic ratio, dynamics)\n"
    "- Artist and track name\n"
    "- A POSITION NUMBER at the start of each line (e.g. '  3. track name — artist')\n\n"
    "Your task: select exactly N anchor tracks that best realise the requested "
    "playlist structure type, and arrange them in the correct order.\n"
    "Choose tracks whose descriptions and features match the energy arc, mood "
    "progression, and dynamic contour described by the structure. "
    "Prioritise diversity of textures and keys.\n\n"
    "OUTPUT FORMAT — strictly follow this structure, return POSITION NUMBERS only, no extra text:\n"
    "ANCHORS:\n"
    "7\n"
    "23\n"
    "41\n"
    "...\n"
    "(exactly N lines of position numbers, one per line, ordered by intended sequence)\n\n"
    "Each number refers to the POSITION NUMBER shown at the start of each track line "
    "in the track list below. Return ONLY the numbers in the ANCHORS: block — "
    "no track names, no artists, no commentary."
)

PLAYLIST_STRUCTURES = [
    {
        "id": "flat",
        "name": "Flat",
        "desc": "Uniform energy throughout — steady, hypnotic, no dramatic shifts.",
        "anchor_pct": 12,
    },
    {
        "id": "rise_fall",
        "name": "Rise and Fall",
        "desc": "Gradual build-up to a single peak, then a slow descent.",
        "anchor_pct": 20,
    },
    {
        "id": "wave",
        "name": "Wave",
        "desc": "Multiple crests and troughs — tension builds, releases, then builds again.",
        "anchor_pct": 25,
    },
    {
        "id": "pulse",
        "name": "Pulse / Peaks",
        "desc": "Alternating high-energy and low-energy blocks, like a heartbeat.",
        "anchor_pct": 18,
    },
    {
        "id": "slow_burn",
        "name": "Slow Burn / Crescendo",
        "desc": "Starts minimal and sparse, steadily accumulates density and intensity.",
        "anchor_pct": 20,
    },
    {
        "id": "rollercoaster",
        "name": "Rollercoaster",
        "desc": "Frequent dynamic swings — intense peaks followed by deep valleys.",
        "anchor_pct": 22,
    },
    {
        "id": "alternating",
        "name": "Alternation / ABAB",
        "desc": "Two contrasting moods or textures trading places back and forth.",
        "anchor_pct": 16,
    },
    {
        "id": "descending",
        "name": "Descending / Cooling",
        "desc": "Starts heavy and intense, gradually unwinds into calm and space.",
        "anchor_pct": 20,
    },
    {
        "id": "ascension",
        "name": "Ascension",
        "desc": "Steady climb from darkness to light, low energy to high energy.",
        "anchor_pct": 20,
    },
    {
        "id": "story",
        "name": "Story Arc",
        "desc": "Introduction → development → climax → resolution — like a narrative.",
        "anchor_pct": 25,
    },
    {
        "id": "custom",
        "name": "Custom",
        "desc": "",
        "anchor_pct": 20,
    },
]
