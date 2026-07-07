"""
tests/test_notifications.py — Mixtape

Regression tests for Issue #4: rating another user's shared song should
notify the original sharer, but the current rate_song() implementation
never calls create_notification().
"""

import pytest
from app import create_app, db
from models import User, Song, Rating
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_rating_scenario(app):
    """Create a song owner, a different rater, and the owner's shared song."""
    with app.app_context():
        owner = User(username="owner", email="owner@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([owner, rater])
        db.session.flush()

        song = Song(title="Test Track", artist="Test Artist", shared_by=owner.id)
        db.session.add(song)
        db.session.commit()

        yield {"owner": owner, "rater": rater, "song": song}


def test_rating_song_notifies_owner(app, seed_rating_scenario):
    """
    Rating another user's shared song should save the Rating and create exactly
    one unread 'song_rated' notification for the owner, naming the rater
    and the song, without notifying the rater.
    """
    with app.app_context():
        owner = seed_rating_scenario["owner"]
        rater = seed_rating_scenario["rater"]
        song = seed_rating_scenario["song"]

        rate_song(rater.id, song.id, 4)

        rating = db.session.query(Rating).filter_by(user_id=rater.id, song_id=song.id).first()
        assert rating is not None
        assert rating.score == 4

        owner_notifications = get_notifications(owner.id)
        assert len(owner_notifications) == 1  # Bug causes this to be 0

        notification = owner_notifications[0]
        assert notification["type"] == "song_rated"
        assert notification["read"] is False
        assert rater.username in notification["body"]
        assert song.title in notification["body"]

        rater_notifications = get_notifications(rater.id)
        assert len(rater_notifications) == 0


@pytest.mark.parametrize("score", [1, 5])
def test_rating_boundary_scores_are_saved(app, seed_rating_scenario, score):
    """Boundary-valid scores (1 and 5) should be accepted and saved."""
    with app.app_context():
        rater = seed_rating_scenario["rater"]
        song = seed_rating_scenario["song"]

        rating = rate_song(rater.id, song.id, score)

        assert rating.score == score
        saved = db.session.query(Rating).filter_by(user_id=rater.id, song_id=song.id).first()
        assert saved.score == score


def test_self_rating_saves_rating_without_self_notification(app, seed_rating_scenario):
    """
    A user rating their own shared song should save the Rating but must not
    notify themselves, matching the self-action check used in add_to_playlist().
    """
    with app.app_context():
        owner = seed_rating_scenario["owner"]
        song = seed_rating_scenario["song"]

        rate_song(owner.id, song.id, 3)

        rating = db.session.query(Rating).filter_by(user_id=owner.id, song_id=song.id).first()
        assert rating is not None
        assert rating.score == 3

        owner_notifications = get_notifications(owner.id)
        assert len(owner_notifications) == 0


@pytest.mark.parametrize("invalid_score", [0, 6])
def test_invalid_scores_raise_and_persist_nothing(app, seed_rating_scenario, invalid_score):
    """Scores outside 1-5 should raise ValueError and create no Rating or Notification."""
    with app.app_context():
        owner = seed_rating_scenario["owner"]
        rater = seed_rating_scenario["rater"]
        song = seed_rating_scenario["song"]

        with pytest.raises(ValueError):
            rate_song(rater.id, song.id, invalid_score)

        rating = db.session.query(Rating).filter_by(user_id=rater.id, song_id=song.id).first()
        assert rating is None

        assert get_notifications(owner.id) == []
        assert get_notifications(rater.id) == []
