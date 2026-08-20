# Verify ambiguous sends against the Gmail Sent folder and resend automatically

Ambiguous Gmail responses (timeouts on slow networks, 5xx, lost responses) used to leave a
Delivery in a terminal **Unknown** state: the only automatic resolution was an Amazon reply
that often never comes, so the user had to check Gmail by hand and resend manually. Kindrop
now stamps every send attempt with a persisted RFC 822 Message-ID, and on an ambiguous
response probes `in:sent rfc822msgid:` to learn whether the message actually left. If it
did, the Delivery becomes `sent_unconfirmed` as usual; if it demonstrably did not (or the
probes themselves keep failing), Kindrop resends automatically — three sends in total, still
≥60 s apart — accepting the risk of an occasional Kindle-side duplicate, which the user
prefers over stuck deliveries. Definite 4xx rejections (other than disguised throttling) are
permanent failures and are never resent blindly, an explicit send timeout prevents slow wifi
from manufacturing ambiguity, and exhaustion marks the Delivery `failed` with the reason
shown. The alternative of a separate asynchronous verification phase was rejected: blocking
the sequential single-user batch for a few minutes is simpler than deferring job completion
and artifact cleanup. The "never claim positive Kindle delivery" invariant is untouched, and
pre-existing Unknown deliveries without a Message-ID are settled as `failed` at startup.
