# Deliver split EPUB artifacts through Gmail and reconcile Amazon email

Kindrop sends KCC-generated EPUB artifacts through the connected Gmail account, targets 20 MB parts to remain below Gmail's attachment limit, and rate-limits delivery to one email per minute. Amazon has no delivery API, so Kindrop reports sent-but-unconfirmed honestly and reads narrowly scoped Amazon messages to classify rejections or follow a strictly validated verification link.

