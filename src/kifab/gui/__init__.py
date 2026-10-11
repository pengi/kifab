"""Graphical frontend (PySide6, optional extra ``kifab[gui]``).

Thin like the CLI: it only calls core services. Long-running work (check,
bundle) runs on a worker thread that reports through the core's
:class:`~kifab.core.reporter.Reporter` protocol.
"""
