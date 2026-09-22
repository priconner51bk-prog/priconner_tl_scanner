from pathlib import Path

import youtube_handoff


def test_handoff_queue_deduplicates_and_acknowledges(tmp_path):
    path = Path(tmp_path) / "youtube.json"

    assert youtube_handoff.enqueue(
        ["https://youtu.be/id1", "https://youtu.be/id1", "u2"], path
    ) == ["https://www.youtube.com/watch?v=id1", "u2"]
    assert youtube_handoff.enqueue(["u2", "u3"], path) == ["u3"]
    assert youtube_handoff.pending(path) == [
        "https://www.youtube.com/watch?v=id1", "u2", "u3"
    ]

    assert youtube_handoff.acknowledge(["u2"], path) == [
        "https://www.youtube.com/watch?v=id1", "u3"
    ]
    assert youtube_handoff.pending(path) == [
        "https://www.youtube.com/watch?v=id1", "u3"
    ]
