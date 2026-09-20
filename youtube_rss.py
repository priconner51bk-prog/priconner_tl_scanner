"""Small, dependency-free YouTube channel RSS adapter.

RSS is intended for upload discovery, not full video metadata extraction.
The returned objects intentionally match the attributes consumed by the
channel scanner's YTDLPVideo/YTDLPChannel objects.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"


@dataclass
class RSSVideo:
    video_id: str
    watch_url: str
    title: str
    description: str = ""
    tags: list = None
    publish_date: datetime | None = None
    channel_id: str = ""
    channel_url: str = ""

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class RSSChannel:
    def __init__(self, channel_id, timeout=15, session=None):
        self.channel_id = channel_id
        self.channel_url = f"https://www.youtube.com/channel/{channel_id}"
        self.feed_url = RSS_URL.format(channel_id)
        client = session or requests
        response = client.get(self.feed_url, timeout=timeout)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        self.channel_name = root.findtext(f"{{{ATOM}}}title", default="")
        self.videos = []
        for entry in root.findall(f"{{{ATOM}}}entry"):
            video_id = entry.findtext(f"{{{YT}}}videoId", default="")
            if not video_id:
                continue
            published = _parse_date(entry.findtext(f"{{{ATOM}}}published"))
            self.videos.append(RSSVideo(
                video_id=video_id,
                watch_url=f"https://www.youtube.com/watch?v={video_id}",
                title=entry.findtext(f"{{{ATOM}}}title", default=""),
                publish_date=published,
                channel_id=channel_id,
                channel_url=self.channel_url,
            ))
        self.entry_count = len(self.videos)


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    return parsed.astimezone(timezone.utc)

