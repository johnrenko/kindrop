# Kindrop

Kindrop prepares personal comic archives from Google Drive for a single Kindle library.

## Language

**Source Folder**:
The folder in My Drive whose descendant comic archives Kindrop inspects without modifying them.
_Avoid_: Inbox, watched folder

**Source Revision**:
A specific immutable version of a comic archive from Drive or a local browser upload, identified by
its source identity and content fingerprint.
_Avoid_: File, comic version

**Scan**:
A user-initiated inspection of the Source Folder that discovers and prepares new Drive File Revisions for review.
_Avoid_: Sync, watch

**Candidate**:
A newly ingested Source Revision that is ready for review before conversion.
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
The process of sending one Artifact to the Kindle Destination and reconciling Amazon's response; it may span several send attempts.
_Avoid_: Email, upload

**Kindle Destination**:
The configured Kindle device profile and Send to Kindle email address.
_Avoid_: Device, recipient

**Manual Upload**:
A user-initiated SFTP transfer of one local file into the folder currently open in the Kindle
storage browser. It is verified and published atomically, but it does not create a Conversion
Job, Artifact, or Delivery record.
_Avoid_: Delivery, automatic delivery
