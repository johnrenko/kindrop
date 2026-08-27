# Deliver optimized books directly to KOReader over SSH

Kindrop uses the Kindle's local KOReader SSH server as its primary delivery transport. The
Kindle is addressed at `192.168.1.53:2222` as `root`, with a configurable existing private-key
path and a pinned `known_hosts` file. The connection remains LAN-only: Kindrop never exposes
the Kindle SSH service to the internet and retries pending work when the Kindle becomes
reachable again.

CBR, CBZ, and image-heavy PDF inputs are optimized for the Paperwhite 1/2 `KPW` profile into
one CBZ. PDF optimization is enabled by default but is reviewable per document; an unoptimized
PDF and an EPUB pass through unchanged. Direct SSH delivery has no email-size split limit.

Kindrop's automatic Delivery pipeline owns only `/mnt/us/documents/KOReader/Kindrop`. It uploads
to a temporary name, verifies the remote SHA-256, and atomically renames to the final path. The complete selected batch is
blocked unless it leaves at least 100 MiB free. Untracked collisions block rather than overwrite,
updates retain their recorded path and adjacent KOReader reading data, and Kindrop never mirrors
Drive deletions or automatically deletes Kindle books.

The SSH storage browser is a separate, operator-controlled tool for explicit actions within
`/mnt/us`. Its Manual Upload copies one selected local file into the currently open folder using
the same hash verification and atomic publication, refuses collisions, and creates no Delivery
or other pipeline record. Browse, rename, move, delete, and Manual Upload actions never run
automatically.

The file payload uses the SSH server's SFTP subsystem rather than SCP because the KOReader
Dropbear bundle does not provide a remote `scp` executable. Metadata checks, hashing, and atomic
publication continue to use strict, key-only SSH commands.

Gmail remains a manual fallback. Selecting **Send by email instead** cancels the incomplete SSH
Delivery and queues a separate Gmail Job which recreates split EPUB artifacts under the existing
Send to Kindle constraints. Kindrop never falls back automatically and warns that Kindle capacity
cannot be checked while it is unreachable.

This replaces Gmail as the default transport while retaining ADRs 0002 and 0003 for the explicit
fallback path.
