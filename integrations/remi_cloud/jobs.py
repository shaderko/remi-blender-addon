"""Compatibility module: the shared SDK owns requests, recovery and downloads."""
import sys
if __package__:
    from .sdk import workflow
else:
    from sdk import workflow
sys.modules[__name__] = workflow
