# Mastodon Stats Setup Guide

Show the follower, following and post counts of a Mastodon account on your board.

## Overview

**What it does:**

- Shows followers, following and posts for any Mastodon account
- Shows how much each count has changed today, with a green or red tile for followers
- Shows the account handle, display name and the date of its last post

**Prerequisites:**

- The account's handle, such as `@alice@mastodon.social`. It can be anyone's public account, not only yours
- The instance must run Mastodon 3.4.0 or later (almost all do)
- An access token only if the instance refuses public lookups (see step 2)

The plugin only reads public profile counts. It never posts or changes anything.

## Quick Setup

### 1. Enable the Plugin

In the FiestaBoard web UI:

1. Go to **Integrations**
2. Find **Mastodon Stats** and toggle it **On**

### 2. Configure the Account

1. Open the plugin's settings
2. Enter the **Account** as `@username@instance`, for example `@alice@mastodon.social`
3. Leave **Access Token** empty unless the plugin later says the instance does not allow public lookups. In that case, on your own instance go to **Preferences > Development > New application**, give it any name, keep only the `read:accounts` scope, save, open the application and copy **Your access token** into this field
4. Save

### 3. Create a Board Template

1. Go to **Pages** and create a new page (or create one from the plugin's demo)
2. Add the variables you want, for example:

```
{{mastodon_stats.handle}}

FOLLOWERS {{mastodon_stats.followers}} {{mastodon_stats.followers_trend_tile}}{{mastodon_stats.followers_change}}
FOLLOWING {{mastodon_stats.following}} {{mastodon_stats.following_change}}
POSTS {{mastodon_stats.posts}} {{mastodon_stats.posts_change}}
```

### 4. View on Your Board

Show the page on your board. Counts refresh every 15 minutes by default.

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `handle` | The account as @username@instance | `@ALICE@EXAMPLE.SOCIAL` |
| `username` | Username without the instance | `ALICE` |
| `display_name` | Profile display name | `ALICE EXAMPLE` |
| `instance` | The instance | `EXAMPLE.SOCIAL` |
| `last_post` | Date of the most recent post | `OCT 1` |
| `followers` / `following` / `posts` | Counts with thousands separators | `12,345` |
| `followers_short` / `following_short` / `posts_short` | Shortened counts | `12.3K` |
| `followers_count` / `following_count` / `posts_count` | Plain numbers | `12345` |
| `followers_change` / `following_change` / `posts_change` | Change today | `+12` |
| `followers_trend_tile` | Green tile up, red tile down | `{66}` |

Prefix each with `mastodon_stats.` in templates.

## Configuration Reference

| Setting | Required | Default | Description |
|---------|----------|---------|-------------|
| Account | Yes | | `@username@instance`, or a profile URL |
| Access Token (optional) | No | | Token with `read:accounts` from your own instance, for instances that refuse public lookups |
| Show Daily Change | No | On | Change since the first check of the day (local time); starts over after a restart |
| Refresh Interval (seconds) | No | 900 | How often to ask the instance (300-86400) |

**Environment variables:** none.

## Troubleshooting

**"Account ... not found"**

- Check the spelling and the instance. The handle is the part after the first `@` on the profile page, then the instance, such as `@alice@mastodon.social`.

**"... does not allow public lookups"**

- The instance requires a signed-in request. Add an access token as described in step 2.

**"... rejected the access token"**

- The token was revoked or lacks `read:accounts`. Create a new one and paste it.

**"... is rate limiting requests"**

- The instance asked FiestaBoard to slow down. The board keeps the last counts and tries again after the wait the instance asked for. Raise the refresh interval if it keeps happening.

**Counts are blank**

- The account hides its counts. Nothing to fix.

**Daily change shows 0 after a restart**

- The day's starting counts are kept in memory, so they start over when FiestaBoard restarts.
