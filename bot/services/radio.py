"""Built-in internet radio stations."""

from __future__ import annotations

RADIO_STATIONS: dict[str, dict[str, str]] = {
  "lofi": {
    "name": "Lofi Girl Radio",
    "url": "https://www.youtube.com/watch?v=jfKfPfyJRdk",
    "genre": "Lo-Fi",
    "emoji": "🎧",
  },
  "jazz": {
    "name": "Smooth Jazz 24/7",
    "url": "https://www.youtube.com/watch?v=Dx5qFachd3A",
    "genre": "Jazz",
    "emoji": "🎷",
  },
  "classical": {
    "name": "Classical Music",
    "url": "https://www.youtube.com/watch?v=jgpJVI3tDbY",
    "genre": "Classical",
    "emoji": "🎻",
  },
  "rock": {
    "name": "Rock Classics 24/7",
    "url": "https://www.youtube.com/watch?v=5yx6BWlEVcY",
    "genre": "Rock",
    "emoji": "🎸",
  },
  "pop": {
    "name": "Today's Top Hits",
    "url": "https://www.youtube.com/watch?v=Cn5PA_yC9ic",
    "genre": "Pop",
    "emoji": "🎤",
  },
  "edm": {
    "name": "EDM Festival Mix",
    "url": "https://www.youtube.com/watch?v=60ItHLz5WEA",
    "genre": "EDM",
    "emoji": "🎛",
  },
  "chill": {
    "name": "Chillhop Radio",
    "url": "https://www.youtube.com/watch?v=5yx6BWlEVcY",
    "genre": "Chill",
    "emoji": "🌿",
  },
  "hiphop": {
    "name": "Hip Hop Radio",
    "url": "https://www.youtube.com/watch?v=36YnV9STBqc",
    "genre": "Hip-Hop",
    "emoji": "🎤",
  },
  "country": {
    "name": "Country Hits",
    "url": "https://www.youtube.com/watch?v=Y2V6u1YQkKo",
    "genre": "Country",
    "emoji": "🤠",
  },
  "news": {
    "name": "BBC World Service",
    "url": "http://stream.live.vc.bbcmedia.co.uk/bbc_world_service",
    "genre": "News",
    "emoji": "📻",
  },
}


def list_stations() -> list[tuple[str, dict[str, str]]]:
    return list(RADIO_STATIONS.items())


def get_station(key: str) -> dict[str, str] | None:
    return RADIO_STATIONS.get(key.lower())


def find_station(query: str) -> dict[str, str] | None:
    q = query.lower().strip()
    if q in RADIO_STATIONS:
        return RADIO_STATIONS[q]
    for key, station in RADIO_STATIONS.items():
        if q in key or q in station["name"].lower() or q in station["genre"].lower():
            return station
    return None
