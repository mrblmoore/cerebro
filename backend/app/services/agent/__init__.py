"""Ask's agent: a tool registry and a bounded tool-calling loop."""

from app.services.agent.registry import REGISTRY, ToolContext, catalog, tool  # noqa: F401
from app.services.agent import tools as _tools  # noqa: F401,E402 - registers the tools
from app.services.agent import integration_tools as _integration_tools  # noqa: F401,E402
from app.services.agent import systems_tools as _systems_tools  # noqa: F401,E402
