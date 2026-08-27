# Smart Series Import

## Goal

Turn a large set of related Candidates into one short review decision without weakening
Kindrop's explicit confirmation and read-only Drive boundaries.

## Required behavior

- Detect series groups deterministically from existing metadata, volume markers, trailing volume
  numbers, and folder paths. A generic root-level `Volume NN` group must require a name.
- Show the ready count, complete known range, internal gaps, and duplicate numbers. Previously
  queued or sent Candidates inform continuity but can never be edited by this flow.
- Let the user accept a confidently detected name or search AniList once for the whole group.
- Apply the confirmed series, inferred volume number, and optional AniList author and cover to
  every ready Candidate in the group through one API request.
- Recompute Kindle titles through the existing series-title rules so SSH delivery creates an
  organized series folder instead of `_Unsorted`.
- Keep per-Candidate metadata editing available for exceptions.
- Never create a Conversion Batch or Delivery from metadata application. **Optimize & send**
  remains a separate, explicit confirmation.

## Safety and recovery

- Reject non-ready or missing Candidates as one failed bulk update; do not partially apply.
- Reject Candidates whose volume number cannot be inferred and reject non-HTTPS covers.
- A failed AniList lookup or bulk update leaves every Candidate available for normal review.
- The feature never modifies Source Folder files or automatically deletes Kindle books.
