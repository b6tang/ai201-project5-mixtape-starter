"""
tests/test_feed.py — Mixtape

Regression test for Issue #2: "Friends Listening Now" should only show
listening events from the past 30 minutes, but get_friends_listening_now()
currently uses a 24-hour RECENT_THRESHOLD.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services import feed_service
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_feed(app):
    """Create a viewer with two mutual friends, each with one listening event."""
    with app.app_context():
        viewer = User(username="viewer", email="viewer@example.com")
        recent_friend = User(username="recent_friend", email="recent@example.com")
        stale_friend = User(username="stale_friend", email="stale@example.com")
        db.session.add_all([viewer, recent_friend, stale_friend])
        db.session.flush()

        # Bidirectional friendships between viewer and each friend
        for a, b in [(viewer, recent_friend), (viewer, stale_friend)]:
            db.session.execute(friendships.insert().values(user_id=a.id, friend_id=b.id))
            db.session.execute(friendships.insert().values(user_id=b.id, friend_id=a.id))

        song_recent = Song(title="Recent Track", artist="Artist A", shared_by=recent_friend.id)
        song_stale = Song(title="Stale Track", artist="Artist B", shared_by=stale_friend.id)
        db.session.add_all([song_recent, song_stale])
        db.session.flush()

        fixed_now = datetime(2024, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

        event_recent = ListeningEvent(
            user_id=recent_friend.id,
            song_id=song_recent.id,
            listened_at=fixed_now - timedelta(minutes=15),
        )
        event_stale = ListeningEvent(
            user_id=stale_friend.id,
            song_id=song_stale.id,
            listened_at=fixed_now - timedelta(minutes=31),
        )
        db.session.add_all([event_recent, event_stale])
        db.session.commit()

        yield {
            "viewer": viewer,
            "recent_friend": recent_friend,
            "stale_friend": stale_friend,
            "fixed_now": fixed_now,
        }


def test_only_shows_friends_listening_within_30_minutes(app, seed_feed, monkeypatch):
    """
    A friend who listened 15 minutes ago should appear in the feed; a friend
    who listened 31 minutes ago should not. Fails under the current
    24-hour threshold, passes once the threshold is 30 minutes.
    """
    with app.app_context():
        viewer = seed_feed["viewer"]
        recent_friend = seed_feed["recent_friend"]
        fixed_now = seed_feed["fixed_now"]

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        monkeypatch.setattr(feed_service, "datetime", FrozenDateTime)

        results = get_friends_listening_now(viewer.id)

        assert len(results) == 1  # Bug (24h threshold) causes this to be 2
        assert results[0]["friend"]["username"] == recent_friend.username
