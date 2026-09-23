"""A local browser dashboard for both providers.

Deliberately built on ``http.server`` rather than a web framework: this tool's
whole dependency list is three packages, and the machines it most needs to run
on -- a locked-down cluster login node, a fresh Windows box -- are exactly where
"just add a dependency" costs the most. Nothing here needs routing, templating
or async; a few endpoints and one page do not justify any of it.

It is a LOCAL dashboard, not a service. It binds loopback, mints a fresh token
per run, and never persists either. See ``server`` for why that matters.
"""
