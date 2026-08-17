# Kindrop

Kindrop prepares personal comic archives from Google Drive for a single Kindle library.

## Language

**Source Folder**:
The folder in My Drive whose descendant comic archives Kindrop inspects without modifying them.
_Avoid_: Inbox, watched folder

**Drive File Revision**:
A specific immutable version of a comic archive, identified by its Drive file identity and content fingerprint.
_Avoid_: File, comic version

**Scan**:
A user-initiated inspection of the Source Folder that discovers and prepares new Drive File Revisions for review.
_Avoid_: Sync, watch

**Candidate**:
A newly discovered Drive File Revision that is ready for review before conversion.
_Avoid_: Pending file, queue item

**Conversion Batch**:
A user-confirmed selection of Candidates that share one Conversion Preset snapshot.
_Avoid_: Upload, run

**Conversion Job**:
The independent processing of one Candidate within a Conversion Batch.
_Avoid_: Task, conversion

**Conversion Preset**:
The reading direction, spread handling, crop behavior, and Kindle profile applied to a Conversion Job.
_Avoid_: KCC flags, options

**Artifact**:
A temporary EPUB produced by a Conversion Job. A large source can produce multiple numbered Artifacts.
_Avoid_: Output, ebook

**Delivery**:
One attempt to send one Artifact to the Kindle Destination and reconcile Amazon's response.
_Avoid_: Email, upload

**Kindle Destination**:
The configured Kindle device profile and Send to Kindle email address.
_Avoid_: Device, recipient

