# Domain glossary

**Channel list**: the monitored channels in `channels.yml`, read and written only through
`channel_list.py`. Each entry is a **Channel**.

**Channel**: one channel list entry: handle, title, channel ID, uploads playlist ID, and the digest
flag. It is written either as a bare `"@handle"` string or as a dict.

**Digest flag**: `digest: true|false` on a Channel. `false` means the channel stays in the list but
is left out of the email. Missing means `true`. Merges and rewrites always keep it.

**Uploads playlist**: the `UU...` playlist that holds every upload of a channel. The digest reads
new videos from it.

**Channel resolution**: turning what the channel list knows about a channel (handle, stored ID,
title) into a verified channel ID and uploads playlist. It tries handle, then ID, then title search.
See `youtube_api.resolve_channel`.

**Playlist probe**: a one-item `playlistItems.list` request that proves an uploads playlist can be
queried. Channel resolution accepts no candidate until the probe passes.

**Stale playlist**: a stored uploads playlist ID that YouTube reports as `playlistNotFound`. At
digest time this triggers channel resolution. `repair_channel_ids.py` makes the fix permanent.

**Subscription merge**: folding the user's current YouTube subscriptions into the channel list.
It refreshes matched entries, appends new ones, and never removes entries.
