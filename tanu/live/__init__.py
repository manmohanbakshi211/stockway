"""Tanu live (layer 2 at cutover): the broker feed turned into minute bars, the same engine as replay (ADR-0004), paper
accounts, alerts and the end-of-day jobs. Nothing here opens a broker connection unless the cutover switch is on
(settings `live`, ADR-0009); the rehearsal runs the whole path against a local fake broker."""
