"""Backward-compatible CLI/import entry point for the unified orchestrator."""
import sys
from services.orchestrator import application
if __name__ == "__main__":
    application.main()
else:
    sys.modules[__name__] = application
