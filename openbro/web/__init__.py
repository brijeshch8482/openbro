"""OpenBro on a web page — a public, safe demo of the agent.

`openbro web` serves a chat page. Each visitor gets their own agent with a
locked-down tool set (web search, date/time, search over the demo
documents), no access to the host's files, memory or shell, and rate
limits. See `openbro/web/demo.py` for what is allowed and why.
"""
