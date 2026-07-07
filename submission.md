
## AI Usage

I used ChatGPT to explain the project structure, including Flask blueprints, SQLAlchemy relationships, and the route-to-service call paths. I also used it to clarify the behavior of Python list slicing and the streak date comparison.

For Issue #4, I used Claude to draft a regression-test file based on the existing pytest style and the provided models and service functions. I reviewed the generated test, removed wording that incorrectly implied a friendship relationship because the fixture did not create one, then ran the test against the buggy code.

For Issue #2, I used Claude to draft `tests/test_feed.py` in the repository's existing pytest style. I reviewed the fixture setup and time-freezing approach, then ran the test against the original 24-hour threshold. It failed because the 31-minute event was incorrectly included, then passed after the threshold was changed to 30 minutes.

I verified AI explanations by running the provided and new tests myself. For Issue #3, the suggested duplicate-search investigation did not reproduce: both the supplied test and seeded API response returned one result. I did not change that service because the runtime evidence did not support a fix.

## Codebase Map

### Application setup

`app.py` creates the Flask application, configures the database URI (defaulting to SQLite), initializes SQLAlchemy, and registers four route blueprints with the URL prefixes `/songs`, `/playlists`, `/users`, and `/feed`.

### Data layer

`models.py` defines the database schema and relationships used throughout the app. The model layer stores application data and describes how records connect to one another; it does not contain the request-handling or feature logic.

The app has seven main SQLAlchemy models:

- `User` stores account information, listening streak state, the user's last listening time, and relationships to friends, shared songs, ratings, listening events, notifications, and playlists.
- `Tag` stores reusable tag names such as `rap` or `hip-hop`.
- `Song` stores song metadata, the user who shared the song, and relationships to ratings, listening events, and tags.
- `ListeningEvent` records that a specific user listened to a specific song at a specific time.
- `Rating` stores one user's numeric score for one song. A unique constraint prevents the same user from creating multiple separate rating records for the same song.
- `Playlist` stores playlist metadata, including its creator and whether it is collaborative.
- `Notification` stores a recipient, notification type, message body, creation time, and read state.

`models.py` also defines three association tables for many-to-many relationships:

- `friendships` connects users to other users.
- `song_tags` connects songs to reusable tags. A song can have zero, one, or multiple tags, and a tag can belong to multiple songs.
- `playlist_entries` connects playlists to songs. In addition to the playlist ID and song ID, it stores `position`, `added_by`, and `added_at`, so playlist songs can be retrieved in an explicit order rather than relying only on insertion order.

The model relationships let services access connected records through the application objects. For example, a `Song` can access its related tags, a `Playlist` can access its songs, and a `User` can access their friends, listening events, ratings, notifications, and playlists.

### Route layer

`routes/` defines the HTTP endpoints for the app.

- `songs.py` handles song search, song details, ratings, and listening events.
- `playlists.py` handles playlist creation, viewing playlists, and adding songs.
- `users.py` handles user profiles, streak lookup, and notifications.
- `feed.py` handles the friends listening-now feed and activity feed.

The routes read request data, call the appropriate service function, and return JSON responses.

### Service layer

`services/` contains the application logic.

- `streak_service.py` records listening events and updates listening streaks.
- `search_service.py` searches songs by title or artist.
- `playlist_service.py` creates playlists and retrieves ordered playlist songs.
- `notification_service.py` creates and retrieves notifications and handles ratings or playlist additions.
- `feed_service.py` builds the listening-now and activity feeds.

### Supporting files

`seed_data.py` creates sample users, friendships, songs, tags, playlists, listening events, streak values, and notifications for local testing.

`tests/` contains focused tests for streak behavior, song search behavior, and playlist retrieval behavior.

### Example data flow: recording a listening event

A client sends `POST /songs/<song_id>/listen` with a `user_id` in the JSON body.

`routes/songs.py` receives the request, reads the `user_id`, and calls `record_listening_event(user_id, song_id)` in `services/streak_service.py`.

`record_listening_event()` loads the user from the database, creates a `ListeningEvent`, calls `update_listening_streak()` to update the user's streak state, commits the changes, and returns the new event.

The route converts the event with `event.to_dict()` and returns it as a JSON response.

--- 
## Root Cause Analysis 

### Issue #1 — My listening streak keeps resetting

**Reproduction:** Before changing code, I checked and ran `pytest tests/test_streaks.py -q`. The test fixture created a new user with no previous listening history. After reviewing `tests/test_streaks.py`, I identified `test_streak_increments_on_sunday` as the appropriate reproduction case because it specifically constructs the reported Saturday-to-Sunday boundary. Rather than relying on a generic streak check, I used this test to place the user in the exact state required to trigger the issue: a prior listen on Saturday followed by a listen on Sunday. Four tests passed and test_streak_increments_on_sunday failed. It called update_listening_streak() first with Saturday, June 15, 2024 at 12:00 UTC, then with Sunday, June 16, 2024 at 12:00 UTC. These are consecutive calendar days, so the expected streak after the second call was 2.
The test failed because the actual streak was 1:
```
FAILED tests/test_streaks.py::test_streak_increments_on_sunday
assert 1 == 2
```
This confirmed the bug before I changed the implementation.

**Navigation Strategy:** I first read `models.py` to identify the relevant `User` fields: `listening_streak` stores the current streak and `last_listened_at` stores the previous listening time. I then traced how `update_listening_streak()` in `services/streak_service.py` uses those fields.

The function converts both timestamps to dates and calculates `days_since_last = (today - last_date).days`, which already handles calendar boundaries such as Saturday-to-Sunday, Sunday-to-Monday, and month changes. A result of `0` means the same calendar day, `1` means the previous calendar day, and a larger value means at least one day was skipped.

The `update_listening_streak()` docstring states that if a user listened yesterday, the streak should increment by `1`, with no Sunday exception. However, the implementation required both `days_since_last == 1` and `today.weekday() != 6`. For the Saturday-to-Sunday case, the dates were one day apart, but Sunday has `weekday() == 6`, so the increment branch was skipped and the reset branch ran instead. Comparing the documented rule and date calculation with that specific condition, then reproducing the failure in `test_streak_increments_on_sunday`, confirmed that the Sunday check was the cause rather than only a suspicious line.

**The root cause:** In `update_listening_streak()`, the consecutive-day condition incorrectly excluded Sunday:
```python
elif days_since_last == 1 and today.weekday() != 6:
    user.listening_streak += 1
```
datetime.weekday() returns 6 for Sunday. The documented streak rule only depends on whether the user listened on the previous calendar day, but the implementation treated a weekday() result of 6 as a reason not to increment.

Therefore, when a user listened on Saturday and then Sunday, days_since_last == 1 was true but today.weekday() != 6 was false. The code skipped the increment branch and entered the else branch, which reset user.listening_streak to 1. This caused the visible Sunday streak-reset bug.

**Fix Description:** I removed the Sunday-specific exclusion so that any consecutive calendar day increments the streak:
```python
elif days_since_last == 1:
    user.listening_streak += 1 
```
This preserves the intended rules: same-day listens do not increment, consecutive days increment, and skipped days reset the streak.
**Side-Effect Check:**After the fix, I reran:`pytest tests/test_streaks.py -q`. The result was 5 passed. This verified both sides of the affected boundary: Saturday-to-Sunday now increments correctly, while skipping a day still resets the streak. The same test suite also confirmed that a new user starts at 1, same-day listens do not double-count, and ordinary consecutive days still increment.

### Issue #2 — Friends Listening Now shows people from yesterday

**Reproduction:** Before changing production code, I reset the local database with `python seed_data.py` and started the Flask app. I identified Darius's user ID, then requested: 
`GET /feed/<darius_id>/listening-now` 
Darius is connected to Nova and Simone through the seeded friendship data. The endpoint returned `count: 2`: Simone had a recent listening event, but Nova's event was approximately two hours old and was still included in the “Listening Now” feed.

I then added `tests/test_feed.py` as a focused regression test before changing the service. The test creates a viewer with two friends, freezes the current time, and gives one friend an event from 15 minutes ago and the other an event from 31 minutes ago. Before the fix, `pytest tests/test_feed.py -q` failed because the function returned both friends instead of only the recent one.

**Navigation Strategy:** I first traced the endpoint from `app.py`, where the feed blueprint is registered under `/feed`, to `routes/feed.py`. The `GET /feed/<user_id>/listening-now` route calls `get_friends_listening_now(user_id)`.

Before opening the service logic, I checked `models.py` to confirm that `User.friends` provides the friend relationships and that `ListeningEvent.listened_at` is the timestamp used to determine recency. I then inspected `seed_data.py`, which creates listening events within the past 30 minutes for the Listening Now view and older events beginning two hours earlier that should not appear after the fix.

Finally, I opened `services/feed_service.py`. I became confident that I had found the specific cause when the service's 24-hour cutoff directly explained why Nova's two-hour-old event passed the database filter and appeared in the endpoint response.

**The root cause:** In `get_friends_listening_now()`, the `RECENT_THRESHOLD` constant was defined as:
`RECENT_THRESHOLD = timedelta(hours=24)`
The function calculated cutoff as the current UTC time minus this threshold, then queried for events where:
`ListeningEvent.listened_at >= cutoff`
This made every event from the previous 24 hours eligible for the Listening Now feed. Nova's event was only about two hours old, so it passed the comparison even though it was outside the intended recent-listening window indicated by the seeded data.

The later deduplication loop only keeps one event per friend; it does not remove stale events. Therefore, it could not correct the overly broad database result set, and the endpoint returned both Simone and Nova.

**Fix Description:** I changed the threshold in services/feed_service.py from 24 hours to 30 minutes:
`RECENT_THRESHOLD = timedelta(minutes=30)`
This changes only the cutoff used by get_friends_listening_now(). Events within the past 30 minutes remain eligible, while older events are excluded before the per-friend deduplication step.

**Side-Effect Check:** I used tests/test_feed.py to verify both sides of the boundary with a fixed clock. The friend with a listening event from 15 minutes earlier was returned, while the friend with an event from 31 minutes earlier was excluded. The test failed before the fix because both records were returned, then passed after the threshold change:
```
pytest tests/test_feed.py -q
1 passed
```
I left get_activity_feed() unchanged because it is a separate feed path whose documented behavior is to return recent friend activity without applying the Listening Now recency filter. This confirmed that narrowing the threshold only affects the intended Listening Now feature.

### Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it

**Reproduction:** Before changing the application code, I added `tests/test_notifications.py` as a regression test for this issue.

The test created an original song sharer, a different user who would rate the song, and a song whose `shared_by` field belonged to the original sharer. It then called:`rate_song(rater.id, song.id, 4)`. The `Rating` was saved, but `get_notifications(owner.id)` returned `[]`.
Before the fix: `pytest tests/test_notifications.py -q`
Result: `1 failed, 5 passed`
The failing assertion was:`assert len(owner_notifications) == 1`
The actual result was `0`, confirming that rating another user's shared song did not notify the original sharer.

**Navigation Strategy:** I traced the rating request from `app.py` to `routes/songs.py`, where `POST /songs/<song_id>/rate` calls `rate_song()` in `services/notification_service.py`.

I checked `models.py` for `Song.shared_by`, `Rating`, and `Notification`. In `notification_service.py`, `add_to_playlist()` already uses `create_notification()` for another user's interaction, while `rate_song()` saved the rating and returned without creating a notification. That identified the missing step.

**The root cause:** `rate_song()` created or updated a `Rating`, called `db.session.commit()`, and returned the rating. It never created a `Notification` for `song.shared_by`.

Therefore, the rating existed successfully, but the original sharer had no notification to retrieve.

**Fix Description:** After saving the rating, I added a check that the rater is not the original sharer: `if song.shared_by != user_id:`
When true, `rate_song()` now calls the existing `create_notification()` helper with type `"song_rated"`. The notification is sent to the song owner and includes the rater's username, song title, and score.

**Side-Effect Check:** I checked the related notification flow after adding the rating notification. The fix reuses the existing `create_notification()` helper, so the new rating notification uses the same persisted notification behavior and unread default.

I ran `pytest tests/test_notifications.py -q` after the fix. The regression tests confirmed that rating another user's shared song creates one unread `song_rated` notification for the original sharer and does not notify the rater. They also confirmed that valid boundary scores of 1 and 5 still save correctly, invalid scores of 0 and 6 raise `ValueError` without persisting a rating or notification, and self-rating does not create a self-notification. I then ran `pytest tests/ -q` to verify that the full existing test suite still passed. 

### Issue #5 — The last song in a playlist never shows up

**Reproduction:**  I reproduced this issue using the repository-provided `tests/test_playlists.py` fixture before changing any code.
The test created one user, five songs named `Track 1` through `Track 5`, and one playlist. It inserted all five songs into `playlist_entries` with positions 1 through 5. It then called `get_playlist_songs(playlist_id)`.
Before the fix, `pytest tests/test_playlists.py -q` produced two failures:
- `test_playlist_returns_all_songs` expected 5 songs but received 4.
- `test_playlist_returns_songs_in_order` expected `Track 1` through `Track 5`, but the returned list ended at `Track 4`.
The empty-playlist test passed.

**Navigation Strategy:**  I started at `app.py`, which registers the playlists blueprint under `/playlists`. I then traced the playlist-song endpoint in `routes/playlists.py`: `GET /playlists/<playlist_id>/songs` calls `get_playlist_songs(playlist_id)` in `services/playlist_service.py`.

Inside `get_playlist_songs()`, I checked the query path and related data structure. The function joins `Song` with the `playlist_entries` association table, filters by `playlist_id`, and orders results by `playlist_entries.position` in ascending order. In `models.py`, `playlist_entries` stores `playlist_id`, `song_id`, and `position`, which explains how playlist membership and order are tracked.

I became confident I found the specific cause when I saw that the database query used `.all()` to collect the ordered songs, but the return statement then used `songs[:-1]`. This slicing happened after the query and unconditionally removed the final item from the returned list.

**The root cause:** The bug was in `get_playlist_songs()` in `services/playlist_service.py`.
The function correctly queried all songs belonging to the playlist and ordered them by `playlist_entries.position`. However, it returned:`return [song.to_dict() for song in songs[:-1]]`. In Python, `songs[:-1]` returns every item except the final item. Therefore, the query retrieved all playlist songs correctly, but the return statement always removed the final song before the response was created.

**Fix Description:** 
I changed the return statement 
from: `return [song.to_dict() for song in songs[:-1]]`
to: `return [song.to_dict() for song in songs]`
I did not change the database query, ordering logic, models, routes, or tests. The fix only removed the slice that excluded the final song.

**Side-Effect Check:**  
After the fix, I ran: `pytest tests/test_playlists.py -q`
Result: `3 passed in 0.50s`
The tests confirmed that a playlist with five songs now returns all five songs, the songs still return in ascending `position` order, and an empty playlist still returns `[]`.
I also later ran the test suite after all fixes: `pytest tests/ -q` Result: `19 passed in 0.68s`.

---
## Stretch: Regression Test

I added `tests/test_notifications.py` for Issue #4.

The core test, `test_rating_song_notifies_owner`, verifies that rating another user's shared song saves the rating and creates one unread `song_rated` notification for the original sharer.

Before the fix, the test failed because `rate_song()` saved the `Rating` but created zero notifications. After the fix, it passed. This test would catch the bug again if the notification-creation step were removed in a future change.

I also added `tests/test_feed.py` for Issue #2. It freezes time and verifies that a 15-minute listening event is included while a 31-minute event is excluded. Under the original 24-hour threshold, both events were returned, so the test failed before the fix and now passes.

## Commit History

```text
763a402 (HEAD -> bugfix/mixtape, origin/bugfix/mixtape) fix: narrow listening now window to 30 minutes
b45091e fix: notify song sharers about ratings
66e8dec fix: return all songs in playlist
5d1f036 fix: correct Sunday boundary condition in streak reset logic
2dfdeaa (origin/main, origin/HEAD, main) Add .gitignore file and update README with setup instructions
7b64551 initial commit
```