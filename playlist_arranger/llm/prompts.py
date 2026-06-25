"""LLM prompts for track descriptions and anchor selection."""

DESCRIPTION_SYSTEM_PROMPT = (
    "You are a music expert and audio analyst. "
    "You receive quantitative audio features extracted directly from a recording "
    "(BPM, key, spectral features, MERT neural-embedding statistics) "
    "together with the track metadata. "
    "Write a concise but vivid English description (3-5 sentences) of the track's "
    "sonic character, mood, energy, and genre hints. "
    "Be specific — mention tempo feel, harmonic colour, texture, and dynamics. "
    "Do NOT invent biographical facts about the artist. "
    "Do NOT start with the track name or artist name as the first word. "
    "Reply with the description only, no preamble."
)

ANCHOR_SYSTEM_PROMPT = (
    "You are a music curator and playlist architect with deep knowledge of "
    "electronic, ambient, downtempo, and experimental music.\n\n"
    "You will receive a list of tracks from a single playlist, each with:\n"
    "- A short audio-based description\n"
    "- Key audio features (BPM, key, loudness, harmonic ratio, dynamics)\n"
    "- Artist and track name\n\n"
    "Your task: select exactly N anchor tracks that best realise the requested "
    "playlist structure type, and arrange them in the correct order.\n"
    "Choose tracks whose descriptions and features match the energy arc, mood "
    "progression, and dynamic contour described by the structure. "
    "Prioritise diversity of textures and keys.\n\n"
    "OUTPUT FORMAT — strictly follow this structure, no extra text:\n"
    "ANCHORS:\n"
    "1. Track Name — Artist\n"
    "2. Track Name — Artist\n"
    "...\n"
    "N. Track Name — Artist"
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
]